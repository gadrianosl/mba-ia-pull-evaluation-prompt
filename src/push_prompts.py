"""
Script para fazer push de prompts otimizados ao LangSmith Prompt Hub.

Este script:
1. Lê os prompts otimizados de prompts/bug_to_user_story_v2.yml
2. Valida os prompts
3. Faz push PÚBLICO para o LangSmith Hub
4. Adiciona metadados (tags, descrição, técnicas utilizadas)

SIMPLIFICADO: Código mais limpo e direto ao ponto.
"""

import os
import sys
from dotenv import load_dotenv
from langchain import hub
from langchain_core.prompts import ChatPromptTemplate
from utils import load_yaml, check_env_vars, print_section_header

load_dotenv()


def push_prompt_to_langsmith(prompt_name: str, prompt_data: dict) -> bool:
    """
    Faz push do prompt otimizado para o LangSmith Hub (PÚBLICO).

    Args:
        prompt_name: Nome do prompt
        prompt_data: Dados do prompt

    Returns:
        True se sucesso, False caso contrário
    """
    try:
        system_prompt = prompt_data.get("system_prompt", "")
        user_prompt = prompt_data.get("user_prompt", "{bug_report}")

        prompt_template = ChatPromptTemplate.from_messages([
            ("system", system_prompt),
            ("human", user_prompt),
        ])

        description = prompt_data.get("description", "Prompt otimizado")
        techniques = prompt_data.get("techniques_applied", [])
        tags = prompt_data.get("tags", [])

        metadata_description = (
            f"{description}\n\n"
            f"Version: {prompt_data.get('version', 'v2')}\n"
            f"Techniques: {', '.join(techniques) if techniques else 'n/a'}\n"
            f"Tags: {', '.join(tags) if tags else 'n/a'}"
        )

        print(f"   Publicando prompt público: {prompt_name}")
        pushed_ref = hub.push(
            repo_full_name=prompt_name,
            object=prompt_template,
            new_repo_description=metadata_description,
            new_repo_is_public=True,
            tags=tags,
        )

        print(f"   ✓ Push concluído: {prompt_name}")
        if pushed_ref:
            print(f"   ↳ Hub ref: {pushed_ref}")
        return True

    except Exception as e:
        print(f"   ❌ Erro no push do prompt '{prompt_name}': {e}")
        return False


def validate_prompt(prompt_data: dict) -> tuple[bool, list]:
    """
    Valida estrutura básica de um prompt (versão simplificada).

    Args:
        prompt_data: Dados do prompt

    Returns:
        (is_valid, errors) - Tupla com status e lista de erros
    """
    errors = []

    required_fields = ["description", "system_prompt", "user_prompt", "version"]
    for field in required_fields:
        if field not in prompt_data:
            errors.append(f"Campo obrigatório faltando: {field}")

    system_prompt = prompt_data.get("system_prompt", "").strip()
    user_prompt = prompt_data.get("user_prompt", "").strip()

    if not system_prompt:
        errors.append("system_prompt está vazio")
    if not user_prompt:
        errors.append("user_prompt está vazio")

    if "{bug_report}" not in user_prompt:
        errors.append("user_prompt deve conter a variável {bug_report}")

    full_text = f"{system_prompt}\n{user_prompt}".upper()
    if "TODO" in full_text:
        errors.append("Prompt ainda contém TODO")

    techniques = prompt_data.get("techniques_applied", [])
    if not isinstance(techniques, list) or len(techniques) < 2:
        errors.append("Mínimo de 2 técnicas requeridas em techniques_applied")

    return (len(errors) == 0, errors)


def main():
    """Função principal"""
    print_section_header("PUSH DE PROMPTS OTIMIZADOS")

    required_vars = ["LANGSMITH_API_KEY"]
    if not check_env_vars(required_vars):
        return 1

    prompts_file = "prompts/bug_to_user_story_v2.yml"
    prompts_data = load_yaml(prompts_file)

    if not prompts_data:
        print(f"❌ Falha ao carregar arquivo: {prompts_file}")
        return 1

    username = os.getenv("USERNAME_LANGSMITH_HUB", "").strip()
    if not username:
        print("⚠️  USERNAME_LANGSMITH_HUB não configurado; tentando publicar sem owner explícito")

    success_count = 0
    total = 0

    for key, prompt_data in prompts_data.items():
        total += 1
        print(f"\nValidando prompt: {key}")

        is_valid, errors = validate_prompt(prompt_data)
        if not is_valid:
            print("   ❌ Prompt inválido:")
            for err in errors:
                print(f"      - {err}")
            continue

        remote_name = f"{username}/{key}" if username else key
        pushed = push_prompt_to_langsmith(remote_name, prompt_data)
        if pushed:
            success_count += 1

    print("\n" + "=" * 50)
    print("RESUMO")
    print("=" * 50)
    print(f"Prompts processados: {total}")
    print(f"Prompts publicados: {success_count}")
    print(f"Prompts com falha: {total - success_count}")

    if success_count == total and total > 0:
        print("\n✅ Todos os prompts foram publicados com sucesso")
        return 0

    print("\n⚠️  Nem todos os prompts foram publicados")
    return 1


if __name__ == "__main__":
    sys.exit(main())
