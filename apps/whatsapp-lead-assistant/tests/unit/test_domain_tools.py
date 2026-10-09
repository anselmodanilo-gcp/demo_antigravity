from __future__ import annotations

from datetime import timedelta

import pytest

from tests.conftest import FIXED_NOW
from wla.agent.domain import (
    Financiamento,
    Lead,
    LeadProfile,
    LeadStatus,
    Prazo,
    income_band,
    monthly_payment,
    required_income,
    score_lead,
)
from wla.agent.tools import ToolContext, execute_tool, tool_declarations
from wla.knowledge import Knowledge


def _ctx(kb: Knowledge) -> ToolContext:
    return ToolContext(lead=Lead(lead_id="a" * 32, wa_id="5511988887777"), kb=kb, now=FIXED_NOW)


# ------------------------------------------------------------------ domínio
def test_monthly_payment_price_table() -> None:
    assert monthly_payment(0, 0.1, 360) == 0.0
    pmt = monthly_payment(100_000, 0.12, 360)
    assert 950 < pmt < 1_000


def test_required_income_mcmv_is_lower(kb: Knowledge) -> None:
    assert required_income(289_900, kb, "MCMV") < required_income(289_900, kb, "SBPE")


def test_score_hot_lead(kb: Knowledge) -> None:
    profile = LeadProfile(
        tipologia="3Q",
        renda_familiar_mensal=20_000,
        prazo_compra=Prazo.ATE_3_MESES,
        usa_fgts=True,
        mora_ou_trabalha_na_regiao=True,
        visita=FIXED_NOW + timedelta(days=2),
    )
    score, temperature, parts = score_lead(profile, kb)
    assert score == 100
    assert temperature == "QUENTE"
    assert parts["renda"] == 30


def test_score_warm_lead(kb: Knowledge) -> None:
    profile = LeadProfile(renda_familiar_mensal=20_000, prazo_compra=Prazo.DE_3_A_6_MESES)
    score, temperature, _ = score_lead(profile, kb)
    assert score == 42
    assert temperature == "MORNO"


def test_score_cold_lead_and_partial_income(kb: Knowledge) -> None:
    profile = LeadProfile(tipologia="3Q", renda_familiar_mensal=1_000, prazo_compra=Prazo.SO_PESQUISANDO)
    score, temperature, parts = score_lead(profile, kb)
    assert (score, temperature, parts["renda"]) == (0, "FRIO", 0)
    need = required_income(429_900, kb)
    partial = LeadProfile(tipologia="3Q", renda_familiar_mensal=int(need * 0.9))
    assert score_lead(partial, kb)[2]["renda"] == 15


def test_score_cash_buyer(kb: Knowledge) -> None:
    profile = LeadProfile(renda_familiar_mensal=1, financiamento=Financiamento.A_VISTA)
    _, _, parts = score_lead(profile, kb)
    assert parts["renda"] == 30
    assert parts["recursos"] == 15


@pytest.mark.parametrize(
    ("income", "band"),
    [
        (None, "nao_informada"),
        (3_000, "ate_4k"),
        (8_000, "4k_8.6k"),
        (10_000, "8.6k_12k"),
        (15_000, "12k_20k"),
        (50_000, "acima_20k"),
    ],
)
def test_income_band(income: int | None, band: str) -> None:
    assert income_band(income) == band


# ------------------------------------------------------------------ ferramentas
def test_tool_declarations_are_gemini_friendly() -> None:
    decls = tool_declarations()
    assert {d["name"] for d in decls} == {
        "atualizar_lead",
        "simular_financiamento",
        "consultar_disponibilidade",
        "agendar_visita",
        "transferir_para_corretor",
        "registrar_resumo",
    }
    text = str(decls)
    assert "$ref" not in text
    assert "anyOf" not in text
    assert "'title'" not in text
    assert all(d["description"] for d in decls)


def test_atualizar_lead_updates_profile_and_score(kb: Knowledge) -> None:
    ctx = _ctx(kb)
    result = execute_tool(
        "atualizar_lead",
        {"nome": "Mariana", "tipologia": "3Q", "renda_familiar_mensal": 20000, "objecao": "preço"},
        ctx,
    )
    assert result["ok"] is True
    assert ctx.lead.profile.nome == "Mariana"
    assert ctx.lead.profile.objecoes == ["preço"]
    assert ctx.lead.status == LeadStatus.EM_QUALIFICACAO
    assert ctx.lead.score == result["score"] == 30


def test_atualizar_lead_rejects_invalid_input(kb: Knowledge) -> None:
    ctx = _ctx(kb)
    assert "erro" in execute_tool("atualizar_lead", {"tipologia": "cobertura"}, ctx)
    invalid = execute_tool("atualizar_lead", {"renda_familiar_mensal": -5}, ctx)
    assert invalid["erro"] == "argumentos inválidos"
    assert "erro" in execute_tool("atualizar_lead", {"campo_inexistente": 1}, ctx)
    assert execute_tool("apagar_banco", {}, ctx) == {"erro": "ferramenta inexistente"}


def test_simular_financiamento(kb: Knowledge) -> None:
    ctx = _ctx(kb)
    mcmv = execute_tool("simular_financiamento", {"tipologia": "2Q", "renda_familiar_mensal": 8000}, ctx)
    assert mcmv["programa"] == "MCMV"
    assert mcmv["entrada"] == round(289_900 * 0.2)
    assert "dentro_do_limite" in mcmv
    sbpe = execute_tool("simular_financiamento", {"tipologia": "3Q", "valor_entrada": 10_000_000 // 10}, ctx)
    assert sbpe["programa"] == "SBPE"
    assert sbpe["valor_financiado"] == 0
    assert "erro" in execute_tool("simular_financiamento", {"tipologia": "X"}, ctx)


def test_consultar_disponibilidade(kb: Knowledge) -> None:
    ctx = _ctx(kb)
    assert len(execute_tool("consultar_disponibilidade", {}, ctx)["plantas"]) == 3
    assert len(execute_tool("consultar_disponibilidade", {"tipologia": "2Q"}, ctx)["plantas"]) == 1


@pytest.mark.parametrize(
    ("when", "ok"),
    [
        ("2026-10-10T10:00:00-03:00", True),  # sábado 10h
        ("2026-10-10T10:00:00", True),  # sem fuso: assume Brasília
        ("2026-10-11T10:00:00-03:00", False),  # domingo
        ("2026-10-06T20:00:00-03:00", False),  # fora do horário
        ("2026-10-01T10:00:00-03:00", False),  # passado
        ("2027-01-10T10:00:00-03:00", False),  # mais de 30 dias
    ],
)
def test_agendar_visita_rules(kb: Knowledge, when: str, ok: bool) -> None:
    ctx = _ctx(kb)
    result = execute_tool("agendar_visita", {"data_hora": when}, ctx)
    assert ("ok" in result) is ok
    assert (ctx.lead.profile.visita is not None) is ok


def test_transferir_e_resumo(kb: Knowledge) -> None:
    ctx = _ctx(kb)
    execute_tool("transferir_para_corretor", {"motivo": "quer negociar"}, ctx)
    execute_tool("registrar_resumo", {"resumo": "Busca 3Q, renda 20k"}, ctx)
    assert ctx.handoff_requested
    assert ctx.lead.status == LeadStatus.ATENDIMENTO_HUMANO
    assert ctx.lead.summary == "Busca 3Q, renda 20k"


def test_knowledge_loads(kb: Knowledge) -> None:
    assert kb.empreendimento == "Residencial Jardins"
    assert kb.cheapest.codigo == "2Q"
    assert kb.unit("nada") is None
    assert "Residencial Jardins" in kb.texto
