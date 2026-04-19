import pytest
from app.infra.llm.prompt_renderer import PromptRenderer


def test_render_sql_gen_template(tmp_path):
    tpl = tmp_path / "sql_gen.j2"
    tpl.write_text(
        "스키마:\n{{ schema }}\n질문: {{ question }}\n"
        "{% for ex in few_shots %}예시: {{ ex.question }} -> {{ ex.sql }}\n{% endfor %}",
        encoding="utf-8",
    )
    renderer = PromptRenderer(prompt_dir=str(tmp_path))
    result = renderer.render(
        "sql_gen",
        schema="TABLE_A col1",
        question="A 설비?",
        few_shots=[{"question": "Q", "sql": "SELECT 1"}],
    )
    assert "TABLE_A" in result
    assert "SELECT 1" in result


def test_missing_template_raises(tmp_path):
    renderer = PromptRenderer(prompt_dir=str(tmp_path))
    with pytest.raises(Exception):
        renderer.render("nonexistent")
