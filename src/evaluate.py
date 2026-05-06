"""
Script COMPLETO para avaliar prompts otimizados.

Este script:
1. Carrega dataset de avaliação de arquivo .jsonl (datasets/bug_to_user_story.jsonl)
2. Cria/atualiza dataset no LangSmith
3. Puxa prompts otimizados do LangSmith Hub (fonte única de verdade)
4. Executa prompts contra o dataset
5. Calcula 5 métricas (Helpfulness, Correctness, F1-Score, Clarity, Precision)
6. Publica resultados no dashboard do LangSmith
7. Exibe resumo no terminal

Suporta múltiplos providers de LLM:
- OpenAI (gpt-4o, gpt-4o-mini)
- Google Gemini (gemini-2.5-flash)

Configure o provider no arquivo .env através da variável LLM_PROVIDER.
"""

import os
import sys
import re
import json
import time
import hashlib
import argparse
from typing import List, Dict, Any
from pathlib import Path
from dotenv import load_dotenv
from langsmith import Client
from langchain import hub
from langchain_core.prompts import ChatPromptTemplate
from utils import check_env_vars, format_score, print_section_header, get_llm as get_configured_llm
from metrics import evaluate_f1_score, evaluate_clarity, evaluate_precision

load_dotenv()

_LAST_GEN_CALL_TS = 0.0


def parse_args() -> argparse.Namespace:
    """Parseia argumentos CLI para modo econômico/final de avaliação."""
    parser = argparse.ArgumentParser(description="Avaliação de prompts com suporte a modo econômico")
    parser.add_argument(
        "--max-examples",
        type=int,
        default=int(os.getenv("EVAL_MAX_EXAMPLES", "0")),
        help="Limita quantidade de exemplos (0 = todos)."
    )
    parser.add_argument(
        "--metrics",
        type=str,
        default=os.getenv("EVAL_METRICS", "f1,clarity,precision"),
        help="Métricas base para avaliar: f1,clarity,precision (ex: f1,precision)."
    )
    parser.add_argument(
        "--cache-file",
        type=str,
        default=os.getenv("EVAL_CACHE_FILE", ".cache/eval_cache.json"),
        help="Arquivo de cache de resultados por exemplo."
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Desabilita leitura/gravação de cache."
    )
    parser.add_argument(
        "--judge-model",
        type=str,
        default="",
        help="Override de EVAL_MODEL para o judge (ex: gpt-4o-mini)."
    )
    parser.add_argument(
        "--final-run",
        action="store_true",
        help="Força avaliação completa (15 exemplos, métricas completas)."
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Pula exemplos já presentes no cache (matching por outputs.reference)."
    )
    parser.add_argument(
        "--rerun-examples",
        type=str,
        default="",
        help="Lista de números de exemplos para forçar rerun, ex: 5,15."
    )
    return parser.parse_args()


def load_cache(cache_file: str) -> Dict[str, Any]:
    """Carrega cache do disco; retorna estrutura padrão se não existir."""
    cache_path = Path(cache_file)
    if not cache_path.exists():
        return {"version": 1, "entries": {}}

    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "entries" not in data:
            return {"version": 1, "entries": {}}
        return data
    except Exception:
        return {"version": 1, "entries": {}}


def save_cache(cache_file: str, cache_data: Dict[str, Any]) -> None:
    """Persiste cache no disco."""
    cache_path = Path(cache_file)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(cache_data, f, ensure_ascii=False, indent=2)


def _safe_json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _get_example_payload(example: Any, field_name: str, default: Any = None) -> Any:
    if hasattr(example, field_name):
        return getattr(example, field_name)
    if isinstance(example, dict):
        return example.get(field_name, default)
    return default


def _load_resume_index_from_status(status_path: str = "EVALUATION_STATUS.md") -> int:
    status_file = Path(status_path)
    if not status_file.exists():
        return 0

    try:
        content = status_file.read_text(encoding="utf-8")
    except Exception:
        return 0

    patterns = [
        r"progresso confirmado ate o item\s+(\d+)",
        r"progresso ate o item\s+(\d+)",
        r"progresso ate\s+(\d+)/\d+",
    ]

    for pattern in patterns:
        matches = re.findall(pattern, content, flags=re.IGNORECASE)
        if matches:
            try:
                return max(int(value) for value in matches)
            except ValueError:
                continue

    return 0


def _parse_rerun_examples(raw_value: str) -> set[int]:
    rerun_examples = set()
    if not raw_value:
        return rerun_examples

    for item in raw_value.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            rerun_examples.add(int(item))
        except ValueError:
            continue

    return rerun_examples


def build_example_cache_key(prompt_name: str, prompt_fingerprint: str, example: Any) -> str:
    """Gera chave de cache estável por prompt+conteúdo do exemplo."""
    inputs = example.inputs if hasattr(example, "inputs") else {}
    outputs = example.outputs if hasattr(example, "outputs") else {}
    payload = "|".join([
        prompt_name,
        prompt_fingerprint,
        _safe_json_dumps(inputs),
        _safe_json_dumps(outputs),
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def extract_retry_delay_from_error(error_message: str) -> float:
    """Extrai o tempo de retry sugerido pelo Gemini API da mensagem de erro."""
    # Procura por "retry in X.XXXs" ou "Please retry in X.XXXs"
    match = re.search(r'retry in ([\d.]+)s', error_message, re.IGNORECASE)
    if match:
        try:
            return float(match.group(1)) + 2.0  # Add buffer
        except ValueError:
            pass
    return None


def invoke_chain_with_throttle(chain, inputs):
    """Invoca chain com throttling para respeitar rate limits do Gemini free tier."""
    global _LAST_GEN_CALL_TS

    provider = os.getenv("LLM_PROVIDER", "openai").lower()
    min_interval = 0.0
    if provider in ["google", "gemini"]:
        min_interval = float(os.getenv("GEMINI_MIN_INTERVAL_SEC", "3.0"))

    now = time.time()
    wait = min_interval - (now - _LAST_GEN_CALL_TS)
    if wait > 0:
        time.sleep(wait)

    retries = 5
    for attempt in range(retries):
        try:
            response = chain.invoke(inputs)
            _LAST_GEN_CALL_TS = time.time()
            return response
        except Exception as e:
            error_text = str(e)
            error_text_lower = error_text.lower()
            
            is_rate_limit = "429" in error_text or "quota" in error_text_lower or "resource_exhausted" in error_text_lower
            
            if is_rate_limit and attempt < retries - 1:
                # Tenta extrair o delay sugerido pelo Gemini
                suggested_delay = extract_retry_delay_from_error(error_text)
                
                if suggested_delay:
                    wait_time = suggested_delay
                    print(f"      ⏳ Quota límite atingido. Aguardando {wait_time:.1f}s conforme sugerido pela API...")
                else:
                    # Fallback: exponential backoff começando em 15s
                    wait_time = 15 * (2 ** attempt)
                    max_wait = 120  # máximo 2 minutos
                    wait_time = min(wait_time, max_wait)
                    print(f"      ⏳ Rate limit detectado. Aguardando {wait_time:.1f}s (tentativa {attempt + 1}/{retries})...")
                
                time.sleep(wait_time)
                continue
            
            raise


def get_llm():
    return get_configured_llm(temperature=0)


def load_dataset_from_jsonl(jsonl_path: str) -> List[Dict[str, Any]]:
    examples = []

    try:
        with open(jsonl_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:  # Ignorar linhas vazias
                    example = json.loads(line)
                    examples.append(example)

        return examples

    except FileNotFoundError:
        print(f"❌ Arquivo não encontrado: {jsonl_path}")
        print("\nCertifique-se de que o arquivo datasets/bug_to_user_story.jsonl existe.")
        return []
    except json.JSONDecodeError as e:
        print(f"❌ Erro ao parsear JSONL: {e}")
        return []
    except Exception as e:
        print(f"❌ Erro ao carregar dataset: {e}")
        return []


def create_evaluation_dataset(client: Client, dataset_name: str, jsonl_path: str) -> str:
    print(f"Criando dataset de avaliação: {dataset_name}...")

    examples = load_dataset_from_jsonl(jsonl_path)

    if not examples:
        print("❌ Nenhum exemplo carregado do arquivo .jsonl")
        return dataset_name

    print(f"   ✓ Carregados {len(examples)} exemplos do arquivo {jsonl_path}")

    try:
        datasets = client.list_datasets(dataset_name=dataset_name)
        existing_dataset = None

        for ds in datasets:
            if ds.name == dataset_name:
                existing_dataset = ds
                break

        if existing_dataset:
            print(f"   ✓ Dataset '{dataset_name}' já existe, usando existente")
            return dataset_name
        else:
            dataset = client.create_dataset(dataset_name=dataset_name)

            for example in examples:
                client.create_example(
                    dataset_id=dataset.id,
                    inputs=example["inputs"],
                    outputs=example["outputs"]
                )

            print(f"   ✓ Dataset criado com {len(examples)} exemplos")
            return dataset_name

    except Exception as e:
        print(f"   ⚠️  Erro ao criar dataset: {e}")
        return dataset_name


def pull_prompt_from_langsmith(prompt_name: str) -> ChatPromptTemplate:
    try:
        print(f"   Puxando prompt do LangSmith Hub: {prompt_name}")
        prompt = hub.pull(prompt_name)
        print(f"   ✓ Prompt carregado com sucesso")
        return prompt

    except Exception as e:
        error_msg = str(e).lower()

        print(f"\n{'=' * 70}")
        print(f"❌ ERRO: Não foi possível carregar o prompt '{prompt_name}'")
        print(f"{'=' * 70}\n")

        if "not found" in error_msg or "404" in error_msg:
            print("⚠️  O prompt não foi encontrado no LangSmith Hub.\n")
            print("AÇÕES NECESSÁRIAS:")
            print("1. Verifique se você já fez push do prompt otimizado:")
            print(f"   python src/push_prompts.py")
            print()
            print("2. Confirme se o prompt foi publicado com sucesso em:")
            print(f"   https://smith.langchain.com/prompts")
            print()
            print(f"3. Certifique-se de que o nome do prompt está correto: '{prompt_name}'")
            print()
            print("4. Se você alterou o prompt no YAML, refaça o push:")
            print(f"   python src/push_prompts.py")
        else:
            print(f"Erro técnico: {e}\n")
            print("Verifique:")
            print("- LANGSMITH_API_KEY está configurada corretamente no .env")
            print("- Você tem acesso ao workspace do LangSmith")
            print("- Sua conexão com a internet está funcionando")

        print(f"\n{'=' * 70}\n")
        raise


def evaluate_prompt_on_example(
    prompt_template: ChatPromptTemplate,
    example: Any,
    llm: Any
) -> Dict[str, Any]:
    try:
        inputs = _get_example_payload(example, "inputs", {})
        outputs = _get_example_payload(example, "outputs", {})

        chain = prompt_template | llm

        response = invoke_chain_with_throttle(chain, inputs)
        answer = response.content

        reference = outputs.get("reference", "") if isinstance(outputs, dict) else ""

        if isinstance(inputs, dict):
            question = inputs.get("question", inputs.get("bug_report", inputs.get("pr_title", "N/A")))
        else:
            question = "N/A"

        return {
            "answer": answer,
            "reference": reference,
            "question": question
        }

    except Exception as e:
        print(f"      ⚠️  Erro ao avaliar exemplo: {e}")
        import traceback
        print(f"      Traceback: {traceback.format_exc()}")
        return {
            "answer": "",
            "reference": "",
            "question": ""
        }


def evaluate_prompt(
    prompt_name: str,
    dataset_name: str,
    client: Client,
    selected_metrics: set,
    max_examples: int,
    cache_data: Dict[str, Any],
    use_cache: bool,
    resume: bool = False,
    rerun_examples: set[int] | None = None
) -> Dict[str, float]:
    print(f"\n🔍 Avaliando: {prompt_name}")

    try:
        prompt_template = pull_prompt_from_langsmith(prompt_name)

        examples = list(client.list_examples(dataset_name=dataset_name))
        total_examples = len(examples)

        if max_examples > 0:
            examples = examples[:max_examples]

        print(f"   Dataset: {len(examples)} exemplos (total: {total_examples})")

        llm = get_llm()

        f1_scores = []
        clarity_scores = []
        precision_scores = []

        prompt_fingerprint = hashlib.sha256(str(prompt_template).encode("utf-8")).hexdigest()[:16]
        cache_hits = 0
        resume_from_index = _load_resume_index_from_status() if resume else 0
        rerun_examples = rerun_examples or set()

        # If resume is requested, build a quick lookup of cached references
        cached_references = set()
        if resume and use_cache:
            for v in cache_data.get("entries", {}).values():
                ref = v.get("reference", "")
                if ref:
                    cached_references.add(ref.strip())

        print("   Avaliando exemplos...")

        for i, example in enumerate(examples, 1):
            force_rerun = i in rerun_examples

            if resume and resume_from_index and i <= resume_from_index:
                if force_rerun:
                    print(f"      [{i}/{len(examples)}] RERUN (override do checkpoint)")
                else:
                    print(f"      [{i}/{len(examples)}] SKIP (checkpoint de status)")
                    cache_hits += 1
                    continue

            # Optionally skip examples already present in cache by matching reference
            if resume and use_cache and not force_rerun:
                example_outputs = _get_example_payload(example, "outputs", {})
                example_ref = ""
                if isinstance(example_outputs, dict):
                    example_ref = example_outputs.get("reference", "")
                elif hasattr(example_outputs, "get"):
                    example_ref = example_outputs.get("reference", "")
                example_ref = str(example_ref).strip()

                if example_ref and example_ref in cached_references:
                    print(f"      [{i}/{len(examples)}] SKIP (cached by reference)")
                    cache_hits += 1
                    continue
            cache_key = build_example_cache_key(prompt_name, prompt_fingerprint, example)
            cached = cache_data.get("entries", {}).get(cache_key, {}) if use_cache and not force_rerun else {}

            if cached.get("answer"):
                result = {
                    "answer": cached.get("answer", ""),
                    "reference": cached.get("reference", ""),
                    "question": cached.get("question", "")
                }
                cache_hits += 1
            else:
                result = evaluate_prompt_on_example(prompt_template, example, llm)

            if result["answer"]:
                f1_score = cached.get("f1_score") if use_cache else None
                clarity_score = cached.get("clarity") if use_cache else None
                precision_score = cached.get("precision") if use_cache else None

                if "f1" in selected_metrics and f1_score is None:
                    f1 = evaluate_f1_score(result["question"], result["answer"], result["reference"])
                    f1_score = f1["score"]

                if "clarity" in selected_metrics and clarity_score is None:
                    clarity = evaluate_clarity(result["question"], result["answer"], result["reference"])
                    clarity_score = clarity["score"]

                if "precision" in selected_metrics and precision_score is None:
                    precision = evaluate_precision(result["question"], result["answer"], result["reference"])
                    precision_score = precision["score"]

                if "f1" in selected_metrics and f1_score is not None:
                    f1_scores.append(float(f1_score))

                if "clarity" in selected_metrics and clarity_score is not None:
                    clarity_scores.append(float(clarity_score))

                if "precision" in selected_metrics and precision_score is not None:
                    precision_scores.append(float(precision_score))

                if use_cache:
                    cache_data.setdefault("entries", {})[cache_key] = {
                        "answer": result["answer"],
                        "reference": result["reference"],
                        "question": result["question"],
                        "f1_score": f1_score,
                        "clarity": clarity_score,
                        "precision": precision_score,
                        "updated_at": int(time.time())
                    }

                f1_print = f"{float(f1_score):.2f}" if f1_score is not None else "-"
                clarity_print = f"{float(clarity_score):.2f}" if clarity_score is not None else "-"
                precision_print = f"{float(precision_score):.2f}" if precision_score is not None else "-"
                print(f"      [{i}/{len(examples)}] F1:{f1_print} Clarity:{clarity_print} Precision:{precision_print}")

        avg_f1 = sum(f1_scores) / len(f1_scores) if "f1" in selected_metrics and f1_scores else 0.0
        avg_clarity = sum(clarity_scores) / len(clarity_scores) if "clarity" in selected_metrics and clarity_scores else 0.0
        avg_precision = sum(precision_scores) / len(precision_scores) if "precision" in selected_metrics and precision_scores else 0.0

        avg_helpfulness = (avg_clarity + avg_precision) / 2 if {"clarity", "precision"}.issubset(selected_metrics) else 0.0
        avg_correctness = (avg_f1 + avg_precision) / 2 if {"f1", "precision"}.issubset(selected_metrics) else 0.0

        if use_cache:
            print(f"   Cache hit (geração/score): {cache_hits}/{len(examples)}")

        return {
            "helpfulness": round(avg_helpfulness, 4),
            "correctness": round(avg_correctness, 4),
            "f1_score": round(avg_f1, 4),
            "clarity": round(avg_clarity, 4),
            "precision": round(avg_precision, 4)
        }

    except Exception as e:
        print(f"   ❌ Erro na avaliação: {e}")
        return {
            "helpfulness": 0.0,
            "correctness": 0.0,
            "f1_score": 0.0,
            "clarity": 0.0,
            "precision": 0.0
        }


def display_results(prompt_name: str, scores: Dict[str, float]) -> bool:
    print("\n" + "=" * 50)
    print(f"Prompt: {prompt_name}")
    print("=" * 50)

    print("\nMétricas Derivadas:")
    print(f"  - Helpfulness: {format_score(scores['helpfulness'], threshold=0.9)}")
    print(f"  - Correctness: {format_score(scores['correctness'], threshold=0.9)}")

    print("\nMétricas Base:")
    print(f"  - F1-Score: {format_score(scores['f1_score'], threshold=0.9)}")
    print(f"  - Clarity: {format_score(scores['clarity'], threshold=0.9)}")
    print(f"  - Precision: {format_score(scores['precision'], threshold=0.9)}")

    selected_base = scores.get("selected_base_metrics", ["f1", "clarity", "precision"])
    is_full_run = set(selected_base) == {"f1", "clarity", "precision"}

    if is_full_run:
        full_keys = ["helpfulness", "correctness", "f1_score", "clarity", "precision"]
        average_score = sum(scores[k] for k in full_keys) / len(full_keys)
    else:
        values = []
        if "f1" in selected_base:
            values.append(scores["f1_score"])
        if "clarity" in selected_base:
            values.append(scores["clarity"])
        if "precision" in selected_base:
            values.append(scores["precision"])
        average_score = sum(values) / len(values) if values else 0.0

    print("\n" + "-" * 50)
    print(f"📊 MÉDIA GERAL: {average_score:.4f}")
    print("-" * 50)

    if is_full_run:
        all_above_threshold = all(score >= 0.9 for key, score in scores.items() if key in ["helpfulness", "correctness", "f1_score", "clarity", "precision"])
        passed = all_above_threshold and average_score >= 0.9
    else:
        all_above_threshold = False
        passed = False

    if passed:
        print(f"\n✅ STATUS: APROVADO - Todas as métricas >= 0.9")
    else:
        if is_full_run:
            print(f"\n❌ STATUS: REPROVADO")
            failed_metrics = [name for name, score in scores.items() if name in ["helpfulness", "correctness", "f1_score", "clarity", "precision"] and score < 0.9]
            if failed_metrics:
                print(f"⚠️  Métricas abaixo de 0.9: {', '.join(failed_metrics)}")
            print(f"⚠️  Média atual: {average_score:.4f} | Necessário: 0.9000")
        else:
            print("\nℹ️  STATUS: ITERAÇÃO PARCIAL (modo econômico)")
            print(f"ℹ️  Métricas avaliadas nesta rodada: {', '.join(selected_base)}")
            print("ℹ️  Use --final-run para validar aprovação oficial com todas as métricas")

    return passed


def main():
    args = parse_args()

    if args.judge_model:
        os.environ["EVAL_MODEL"] = args.judge_model

    selected_metrics = {m.strip().lower() for m in args.metrics.split(",") if m.strip()}
    valid_metrics = {"f1", "clarity", "precision"}
    selected_metrics = selected_metrics.intersection(valid_metrics)
    if not selected_metrics:
        selected_metrics = {"f1", "clarity", "precision"}

    max_examples = max(0, args.max_examples)

    if args.final_run:
        selected_metrics = {"f1", "clarity", "precision"}
        max_examples = 0

    print_section_header("AVALIAÇÃO DE PROMPTS OTIMIZADOS")

    provider = os.getenv("LLM_PROVIDER", "openai")
    llm_model = os.getenv("LLM_MODEL", "gpt-4o-mini")
    eval_model = os.getenv("EVAL_MODEL", "gpt-4o")

    print(f"Provider: {provider}")
    print(f"Modelo Principal: {llm_model}")
    print(f"Modelo de Avaliação: {eval_model}\n")
    print(f"Métricas base selecionadas: {', '.join(sorted(selected_metrics))}")
    print(f"Limite de exemplos: {'todos' if max_examples == 0 else max_examples}")
    print(f"Cache: {'desabilitado' if args.no_cache else args.cache_file}\n")

    required_vars = ["LANGSMITH_API_KEY", "LLM_PROVIDER"]
    if provider == "openai":
        required_vars.append("OPENAI_API_KEY")
    elif provider in ["google", "gemini"]:
        required_vars.append("GOOGLE_API_KEY")

    if not check_env_vars(required_vars):
        return 1

    client = Client()
    project_name = os.getenv("LANGSMITH_PROJECT", "prompt-optimization-challenge-resolved")

    jsonl_path = "datasets/bug_to_user_story.jsonl"

    if not Path(jsonl_path).exists():
        print(f"❌ Arquivo de dataset não encontrado: {jsonl_path}")
        print("\nCertifique-se de que o arquivo existe antes de continuar.")
        return 1

    dataset_name = f"{project_name}-eval"
    create_evaluation_dataset(client, dataset_name, jsonl_path)

    cache_data = {"version": 1, "entries": {}}
    use_cache = not args.no_cache
    if use_cache:
        cache_data = load_cache(args.cache_file)

    print("\n" + "=" * 70)
    print("PROMPTS PARA AVALIAR")
    print("=" * 70)
    print("\nEste script irá puxar prompts do LangSmith Hub.")
    print("Certifique-se de ter feito push dos prompts antes de avaliar:")
    print("  python src/push_prompts.py\n")

    username = os.getenv("USERNAME_LANGSMITH_HUB", "").strip()
    if username:
        prompts_to_evaluate = [
            f"{username}/bug_to_user_story_v2",
        ]
    else:
        print("⚠️  USERNAME_LANGSMITH_HUB não configurada; tentando nome sem owner explícito")
        prompts_to_evaluate = [
            "bug_to_user_story_v2",
        ]

    all_passed = True
    evaluated_count = 0
    results_summary = []

    for prompt_name in prompts_to_evaluate:
        evaluated_count += 1

        try:
            scores = evaluate_prompt(
                prompt_name,
                dataset_name,
                client,
                selected_metrics=selected_metrics,
                max_examples=max_examples,
                cache_data=cache_data,
                use_cache=use_cache,
                resume=args.resume,
                rerun_examples=_parse_rerun_examples(args.rerun_examples)
            )
            scores["selected_base_metrics"] = sorted(list(selected_metrics))

            passed = display_results(prompt_name, scores)
            all_passed = all_passed and passed

            results_summary.append({
                "prompt": prompt_name,
                "scores": scores,
                "passed": passed
            })

        except Exception as e:
            print(f"\n❌ Falha ao avaliar '{prompt_name}': {e}")
            all_passed = False

            results_summary.append({
                "prompt": prompt_name,
                "scores": {
                    "helpfulness": 0.0,
                    "correctness": 0.0,
                    "f1_score": 0.0,
                    "clarity": 0.0,
                    "precision": 0.0
                },
                "passed": False
            })

    print("\n" + "=" * 50)
    print("RESUMO FINAL")
    print("=" * 50 + "\n")

    if evaluated_count == 0:
        print("⚠️  Nenhum prompt foi avaliado")
        return 1

    print(f"Prompts avaliados: {evaluated_count}")
    print(f"Aprovados: {sum(1 for r in results_summary if r['passed'])}")
    print(f"Reprovados: {sum(1 for r in results_summary if not r['passed'])}\n")

    if use_cache:
        save_cache(args.cache_file, cache_data)
        print(f"Cache salvo em: {args.cache_file}\n")

    if all_passed and set(selected_metrics) == {"f1", "clarity", "precision"}:
        print("✅ Todos os prompts atingiram todas as métricas >= 0.9!")
        print(f"\n✓ Confira os resultados em:")
        print(f"  https://smith.langchain.com/projects/{project_name}")
        print("\nPróximos passos:")
        print("1. Documente o processo no README.md")
        print("2. Capture screenshots das avaliações")
        print("3. Faça commit e push para o GitHub")
        return 0
    elif set(selected_metrics) != {"f1", "clarity", "precision"}:
        print("ℹ️  Rodada parcial concluída (modo econômico)")
        print("\nPróximos passos:")
        print("1. Ajuste o prompt conforme os scores desta rodada")
        print("2. Reavalie com cache para reduzir custo")
        print("3. Execute rodada oficial: python src/evaluate.py --final-run")
        return 0
    else:
        print("⚠️  Alguns prompts não atingiram todas as métricas >= 0.9")
        print("\nPróximos passos:")
        print("1. Refatore os prompts com score baixo")
        print("2. Faça push novamente: python src/push_prompts.py")
        print("3. Execute: python src/evaluate.py novamente")
        return 1

if __name__ == "__main__":
    sys.exit(main())
