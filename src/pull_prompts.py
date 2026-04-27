"""
Script para fazer pull de prompts do LangSmith Prompt Hub.

Este script:
1. Conecta ao LangSmith usando credenciais do .env
2. Faz pull dos prompts do Hub
3. Salva localmente em prompts/bug_to_user_story_v1.yml

SIMPLIFICADO: Usa serialização nativa do LangChain para extrair prompts.
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from langchain import hub
from utils import save_yaml, check_env_vars, print_section_header

load_dotenv()


def pull_prompts_from_langsmith():
    """Faz pull do prompt v1 do Hub e salva em YAML local."""
    print_section_header("PULL DE PROMPTS DO LANGSMITH")

    required_vars = ["LANGSMITH_API_KEY"]
    if not check_env_vars(required_vars):
        return False

    prompt_name = "leonanluppi/bug_to_user_story_v1"
    output_path = Path("prompts/bug_to_user_story_v1.yml")

    try:
        print(f"Puxando prompt: {prompt_name}")
        prompt = hub.pull(prompt_name)

        system_prompt = ""
        user_prompt = "{bug_report}"

        if hasattr(prompt, "messages"):
            for message_template in prompt.messages:
                template_text = getattr(message_template, "prompt", None)
                if template_text is not None:
                    template_text = getattr(template_text, "template", "")
                else:
                    template_text = getattr(message_template, "template", "")

                role = ""
                prompt_type = getattr(message_template, "prompt", None)
                if prompt_type is not None:
                    role = getattr(prompt_type, "__class__", type(prompt_type)).__name__.lower()

                class_name = message_template.__class__.__name__.lower()

                if "system" in class_name or "system" in role:
                    system_prompt = template_text
                elif "human" in class_name or "user" in class_name:
                    user_prompt = template_text

        prompt_data = {
            "bug_to_user_story_v1": {
                "description": "Prompt para converter relatos de bugs em User Stories",
                "system_prompt": system_prompt.strip() if system_prompt else "",
                "user_prompt": user_prompt.strip() if user_prompt else "{bug_report}",
                "version": "v1",
                "source": prompt_name,
                "tags": ["bug-analysis", "user-story", "product-management"]
            }
        }

        saved = save_yaml(prompt_data, str(output_path))
        if not saved:
            return False

        print(f"✓ Prompt salvo em: {output_path}")
        return True

    except Exception as e:
        print(f"❌ Erro ao fazer pull do prompt: {e}")
        return False


def main():
    """Função principal"""
    success = pull_prompts_from_langsmith()
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
