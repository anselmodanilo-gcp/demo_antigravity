"""Modelo de domínio do lead e regra determinística de qualificação."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from wla.common.models import Temperature
from wla.knowledge import Knowledge


class Prazo(StrEnum):
    ATE_3_MESES = "ate_3_meses"
    DE_3_A_6_MESES = "de_3_a_6_meses"
    MAIS_DE_6_MESES = "mais_de_6_meses"
    SO_PESQUISANDO = "so_pesquisando"


class Financiamento(StrEnum):
    SBPE = "SBPE"
    MCMV = "MCMV"
    A_VISTA = "a_vista"
    NAO_SABE = "nao_sabe"


class LeadStatus(StrEnum):
    NOVO = "novo"
    EM_QUALIFICACAO = "em_qualificacao"
    ATENDIMENTO_HUMANO = "atendimento_humano"
    DESCARTADO = "descartado"


class LeadProfile(BaseModel):
    """Dados coletados na conversa. Sem CPF ou documentos (minimização — LGPD)."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    nome: str | None = Field(default=None, max_length=80)
    tipologia: str | None = Field(default=None, pattern=r"^(2Q|2Q_SUITE|3Q)$")
    renda_familiar_mensal: int | None = Field(default=None, ge=0, le=1_000_000)
    usa_fgts: bool | None = None
    possui_entrada: bool | None = None
    financiamento: Financiamento | None = None
    prazo_compra: Prazo | None = None
    mora_ou_trabalha_na_regiao: bool | None = None
    regiao_atual: str | None = Field(default=None, max_length=80)
    objecoes: list[str] = Field(default_factory=list, max_length=10)
    visita: datetime | None = None


class Lead(BaseModel):
    model_config = ConfigDict(extra="ignore")

    lead_id: str
    wa_id: str
    channel: str = "whatsapp"
    profile_name: str = ""
    status: LeadStatus = LeadStatus.NOVO
    profile: LeadProfile = Field(default_factory=LeadProfile)
    score: int = 0
    temperature: Temperature = "FRIO"
    broker_notified: bool = False
    notice_sent: bool = False
    message_count: int = 0
    summary: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None


# ----------------------------------------------------------------- financiamento
def monthly_payment(principal: float, annual_rate: float, months: int) -> float:
    """Tabela Price: parcela fixa mensal."""
    if principal <= 0:
        return 0.0
    i = (1 + annual_rate) ** (1 / 12) - 1
    return float(principal * i / (1 - (1 + i) ** -months))


def required_income(price: float, kb: Knowledge, program: str = "SBPE") -> float:
    rules = kb.financiamento
    rate = rules.taxas_anuais.get(program, rules.taxas_anuais["SBPE"])
    pmt = monthly_payment(price * rules.percentual_financiado_padrao, rate, rules.prazo_meses_padrao)
    return pmt / rules.comprometimento_maximo_renda


# ----------------------------------------------------------------- score
SCORE_WEIGHTS = {"renda": 30, "prazo": 25, "visita": 20, "recursos": 15, "regiao": 10}
HOT_THRESHOLD = 70
WARM_THRESHOLD = 40


def score_lead(profile: LeadProfile, kb: Knowledge) -> tuple[int, Temperature, dict[str, int]]:
    """Pontuação explicável (0–100). O modelo apenas extrai dados; a nota é calculada aqui."""
    parts = dict.fromkeys(SCORE_WEIGHTS, 0)

    if profile.renda_familiar_mensal:
        unit = kb.unit(profile.tipologia) or kb.cheapest
        program = (
            "MCMV"
            if (unit.elegivel_mcmv and profile.renda_familiar_mensal <= kb.financiamento.mcmv_renda_maxima)
            else "SBPE"
        )
        need = required_income(unit.preco_a_partir, kb, program)
        if profile.financiamento == Financiamento.A_VISTA or profile.renda_familiar_mensal >= need:
            parts["renda"] = SCORE_WEIGHTS["renda"]
        elif profile.renda_familiar_mensal >= need * 0.85:
            parts["renda"] = SCORE_WEIGHTS["renda"] // 2

    prazo_points = {
        Prazo.ATE_3_MESES: SCORE_WEIGHTS["prazo"],
        Prazo.DE_3_A_6_MESES: 12,
        Prazo.MAIS_DE_6_MESES: 5,
        Prazo.SO_PESQUISANDO: 0,
    }
    if profile.prazo_compra:
        parts["prazo"] = prazo_points[profile.prazo_compra]

    if profile.visita:
        parts["visita"] = SCORE_WEIGHTS["visita"]

    if profile.usa_fgts or profile.possui_entrada or profile.financiamento == Financiamento.A_VISTA:
        parts["recursos"] = SCORE_WEIGHTS["recursos"]

    if profile.mora_ou_trabalha_na_regiao:
        parts["regiao"] = SCORE_WEIGHTS["regiao"]

    total = sum(parts.values())
    temperature: Temperature = (
        "QUENTE" if total >= HOT_THRESHOLD else "MORNO" if total >= WARM_THRESHOLD else "FRIO"
    )
    return total, temperature, parts


def income_band(income: int | None) -> str:
    """Faixa de renda para analytics (não guarda o valor exato no BigQuery)."""
    if income is None:
        return "nao_informada"
    for limit, label in ((4_000, "ate_4k"), (8_600, "4k_8.6k"), (12_000, "8.6k_12k"), (20_000, "12k_20k")):
        if income <= limit:
            return label
    return "acima_20k"
