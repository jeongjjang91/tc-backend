from pathlib import Path
from jinja2 import Environment, FileSystemLoader, TemplateNotFound
from app.shared.exceptions import ConfigError


class PromptRenderer:
    def __init__(self, prompt_dir: str = "config/prompts"):
        self._env = Environment(
            loader=FileSystemLoader(prompt_dir, encoding="utf-8"),
            trim_blocks=True,
            lstrip_blocks=True,
        )

    def render(self, template_name: str, **kwargs) -> str:
        try:
            tpl = self._env.get_template(f"{template_name}.j2")
            return tpl.render(**kwargs)
        except TemplateNotFound:
            raise ConfigError(f"Prompt template not found: {template_name}.j2")
