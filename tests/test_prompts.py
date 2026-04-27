"""
Testes automatizados para validação de prompts.
"""
import pytest
import yaml
import sys
from pathlib import Path

# Adicionar src ao path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from utils import validate_prompt_structure

def load_prompts(file_path: str):
    """Carrega prompts do arquivo YAML."""
    with open(file_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)

class TestPrompts:
    def test_prompt_has_system_prompt(self):
        """Verifica se o campo 'system_prompt' existe e não está vazio."""
        data = load_prompts("prompts/bug_to_user_story_v2.yml")
        prompt = data.get("bug_to_user_story_v2", {})

        assert "system_prompt" in prompt
        assert isinstance(prompt["system_prompt"], str)
        assert prompt["system_prompt"].strip() != ""

    def test_prompt_has_role_definition(self):
        """Verifica se o prompt define uma persona (ex: "Você é um Product Manager")."""
        data = load_prompts("prompts/bug_to_user_story_v2.yml")
        prompt = data.get("bug_to_user_story_v2", {})
        system_prompt = prompt.get("system_prompt", "").lower()

        role_markers = [
            "você é",
            "product manager",
            "business analyst",
            "senior product manager",
            "especializado"
        ]

        assert any(marker in system_prompt for marker in role_markers)

    def test_prompt_mentions_format(self):
        """Verifica se o prompt exige formato Markdown ou User Story padrão."""
        data = load_prompts("prompts/bug_to_user_story_v2.yml")
        prompt = data.get("bug_to_user_story_v2", {})
        system_prompt = prompt.get("system_prompt", "").lower()
        user_prompt = prompt.get("user_prompt", "").lower()
        combined = f"{system_prompt}\n{user_prompt}"

        assert "markdown" in combined or "user story" in combined
        assert "como um" in combined and "eu quero" in combined and "para que" in combined

    def test_prompt_has_few_shot_examples(self):
        """Verifica se o prompt contém exemplos de entrada/saída (técnica Few-shot)."""
        data = load_prompts("prompts/bug_to_user_story_v2.yml")
        prompt = data.get("bug_to_user_story_v2", {})
        system_prompt = prompt.get("system_prompt", "")

        has_example_keyword = "exemplo" in system_prompt.lower()
        has_input_output_pattern = (
            ("Entrada:" in system_prompt and "Saída esperada:" in system_prompt)
            or ("Input:" in system_prompt and "Output:" in system_prompt)
        )

        assert has_example_keyword
        assert has_input_output_pattern

    def test_prompt_no_todos(self):
        """Garante que você não esqueceu nenhum `[TODO]` no texto."""
        data = load_prompts("prompts/bug_to_user_story_v2.yml")
        prompt = data.get("bug_to_user_story_v2", {})

        text_parts = [
            prompt.get("description", ""),
            prompt.get("system_prompt", ""),
            prompt.get("user_prompt", ""),
        ]
        complete_text = "\n".join(text_parts).upper()

        assert "TODO" not in complete_text
        assert "[TODO]" not in complete_text

    def test_minimum_techniques(self):
        """Verifica (através dos metadados do yaml) se pelo menos 2 técnicas foram listadas."""
        data = load_prompts("prompts/bug_to_user_story_v2.yml")
        prompt = data.get("bug_to_user_story_v2", {})

        techniques = prompt.get("techniques_applied", [])
        assert isinstance(techniques, list)
        assert len(techniques) >= 2

        is_valid, errors = validate_prompt_structure(prompt)
        if not is_valid:
            pytest.fail(f"Estrutura inválida: {errors}")

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])