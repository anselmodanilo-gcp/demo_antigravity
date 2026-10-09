"""Ferramentas (function calling) disponíveis ao Gemini.

Segurança: lista fechada de ferramentas, argumentos validados no servidor (Pydantic) e regras
de negócio calculadas em código. O modelo nunca define preço, desconto ou nota do lead.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from wla.agent.domain import Financiamento, Lead, LeadStatus, Prazo, monthly_payment, score_lead
from wla.knowledge import Knowledge

log = logging.getLogger(__name__)
TZ = ZoneInfo("America/Sao_Paulo")


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AtualizarLeadArgs(_Args):
    """Registra dados do cliente assim que forem mencionados na conversa."""

    nome: str | None = Field(default=None, max_length=80, description="Primeiro nome do cliente")
    tipologia: str | None = Field(default=None, description="Planta de interesse: 2Q, 2Q_SUITE ou 3Q")
    renda_familiar_mensal: int | None = Field(
        default=None, ge=0, le=1_000_000, description="Renda familiar bruta mensal em reais"
    )
    usa_fgts: bool | None = Field(default=None, description="Pretende usar FGTS")
    possui_entrada: bool | None = Field(default=None, description="Tem valor para a entrada")
    financiamento: Financiamento | None = Field(default=None, description="SBPE, MCMV, a_vista ou nao_sabe")
    prazo_compra: Prazo | None = Field(
        default=None, description="ate_3_meses, de_3_a_6_meses, mais_de_6_meses ou so_pesquisando"
    )
    mora_ou_trabalha_na_regiao: bool | None = Field(default=None, description="Mora ou trabalha em Campinas")
    regiao_atual: str | None = Field(default=None, max_length=80, description="Cidade/bairro onde mora")
    objecao: str | None = Field(default=None, max_length=120, description="Objeção ou preocupação citada")


class SimularFinanciamentoArgs(_Args):
    """Simula a parcela mensal estimada do financiamento."""

    tipologia: str = Field(description="2Q, 2Q_SUITE ou 3Q")
    renda_familiar_mensal: int | None = Field(default=None, ge=0, le=1_000_000)
    valor_entrada: int | None = Field(default=None, ge=0, le=5_000_000, description="Entrada em reais")


class ConsultarDisponibilidadeArgs(_Args):
    """Consulta plantas, preços a partir de e unidades disponíveis."""

    tipologia: str | None = Field(default=None, description="2Q, 2Q_SUITE, 3Q ou vazio para todas")


class AgendarVisitaArgs(_Args):
    """Agenda visita ao apartamento decorado."""

    data_hora: datetime = Field(description="Data e hora no formato ISO 8601, horário de Brasília")


class TransferirParaCorretorArgs(_Args):
    """Passa o atendimento para um corretor humano."""

    motivo: str = Field(max_length=200)


class RegistrarResumoArgs(_Args):
    """Atualiza o resumo do lead para o corretor (1 a 2 frases, sem dados sensíveis)."""

    resumo: str = Field(max_length=400)


TOOL_MODELS: dict[str, type[_Args]] = {
    "atualizar_lead": AtualizarLeadArgs,
    "simular_financiamento": SimularFinanciamentoArgs,
    "consultar_disponibilidade": ConsultarDisponibilidadeArgs,
    "agendar_visita": AgendarVisitaArgs,
    "transferir_para_corretor": TransferirParaCorretorArgs,
    "registrar_resumo": RegistrarResumoArgs,
}


def _simplify(node: Any, defs: dict[str, Any]) -> Any:
    """Resolve $ref e reduz anyOf[X, null] para X (subconjunto de JSON Schema aceito pelo Gemini)."""
    if isinstance(node, list):
        return [_simplify(n, defs) for n in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        return _simplify(defs[node["$ref"].rsplit("/", 1)[-1]], defs)
    if "anyOf" in node:
        options = [o for o in node["anyOf"] if o.get("type") != "null"]
        if len(options) == 1:
            rest = {k: v for k, v in node.items() if k != "anyOf"}
            return {**_simplify(options[0], defs), **_simplify(rest, defs)}
    drop = {"title", "default", "$defs"}
    return {k: _simplify(v, defs) for k, v in node.items() if k not in drop}


def tool_declarations() -> list[dict[str, Any]]:
    """Declarações em JSON Schema geradas a partir dos modelos de validação."""
    decls = []
    for name, model in TOOL_MODELS.items():
        schema = model.model_json_schema()
        params = _simplify(schema, schema.get("$defs", {}))
        decls.append({"name": name, "description": (model.__doc__ or "").strip(), "parameters": params})
    return decls


@dataclass
class ToolContext:
    lead: Lead
    kb: Knowledge
    now: datetime = field(default_factory=lambda: datetime.now(UTC))
    handoff_requested: bool = False
    changed: bool = False


def execute_tool(name: str, raw_args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """Executa uma ferramenta com argumentos validados; erros voltam ao modelo como dados."""
    model = TOOL_MODELS.get(name)
    if model is None:
        log.warning("ferramenta desconhecida solicitada", extra={"tool": name})
        return {"erro": "ferramenta inexistente"}
    try:
        args = model.model_validate(raw_args or {})
    except ValidationError as exc:
        return {
            "erro": "argumentos inválidos",
            "detalhes": exc.errors(include_url=False, include_input=False),
        }
    handler = _HANDLERS[name]
    result = handler(args, ctx)
    log.info("ferramenta executada", extra={"tool": name, "lead_id": ctx.lead.lead_id})
    return result


def _rescore(ctx: ToolContext) -> dict[str, Any]:
    score, temperature, parts = score_lead(ctx.lead.profile, ctx.kb)
    ctx.lead.score, ctx.lead.temperature = score, temperature
    return {"score": score, "temperatura": temperature, "criterios": parts}


def _atualizar_lead(args: AtualizarLeadArgs, ctx: ToolContext) -> dict[str, Any]:
    data = args.model_dump(exclude_none=True)
    objecao = data.pop("objecao", None)
    if "tipologia" in data and ctx.kb.unit(data["tipologia"]) is None:
        return {"erro": "tipologia inválida; use 2Q, 2Q_SUITE ou 3Q"}
    for key, value in data.items():
        setattr(ctx.lead.profile, key, value)
    if objecao and objecao not in ctx.lead.profile.objecoes:
        ctx.lead.profile.objecoes = [*ctx.lead.profile.objecoes, objecao][-10:]
    if ctx.lead.status == LeadStatus.NOVO:
        ctx.lead.status = LeadStatus.EM_QUALIFICACAO
    ctx.changed = True
    return {"ok": True, **_rescore(ctx)}


def _simular(args: SimularFinanciamentoArgs, ctx: ToolContext) -> dict[str, Any]:
    unit = ctx.kb.unit(args.tipologia)
    if unit is None:
        return {"erro": "tipologia inválida; use 2Q, 2Q_SUITE ou 3Q"}
    rules = ctx.kb.financiamento
    renda = args.renda_familiar_mensal or ctx.lead.profile.renda_familiar_mensal
    mcmv = bool(unit.elegivel_mcmv and renda and renda <= rules.mcmv_renda_maxima)
    program = "MCMV" if mcmv else "SBPE"
    price = unit.preco_a_partir
    entrada = (
        args.valor_entrada
        if args.valor_entrada is not None
        else round(price * (1 - rules.percentual_financiado_padrao))
    )
    entrada = min(entrada, price)
    financiado = price - entrada
    parcela = monthly_payment(financiado, rules.taxas_anuais[program], rules.prazo_meses_padrao)
    result: dict[str, Any] = {
        "planta": unit.nome,
        "valor_a_partir": price,
        "entrada": entrada,
        "valor_financiado": financiado,
        "programa": program,
        "taxa_anual_estimada": rules.taxas_anuais[program],
        "prazo_meses": rules.prazo_meses_padrao,
        "parcela_estimada": round(parcela, 2),
        "entrada_parcelavel_em_ate": rules.entrada_parcelas_max,
        "aviso": "Estimativa sem valor de proposta; depende de análise de crédito do banco.",
    }
    if renda:
        result["comprometimento_renda"] = round(parcela / renda, 3)
        result["dentro_do_limite"] = parcela <= renda * rules.comprometimento_maximo_renda
    return result


def _disponibilidade(args: ConsultarDisponibilidadeArgs, ctx: ToolContext) -> dict[str, Any]:
    units = [u for u in ctx.kb.unidades if not args.tipologia or u.codigo == args.tipologia]
    return {
        "empreendimento": ctx.kb.empreendimento,
        "entrega_prevista": ctx.kb.entrega_prevista,
        "plantas": [u.model_dump() for u in units],
    }


def _agendar(args: AgendarVisitaArgs, ctx: ToolContext) -> dict[str, Any]:
    when = args.data_hora if args.data_hora.tzinfo else args.data_hora.replace(tzinfo=TZ)
    local = when.astimezone(TZ)
    showroom = ctx.kb.decorado
    if when <= ctx.now:
        return {"erro": "a data precisa ser no futuro"}
    if when > ctx.now + timedelta(days=30):
        return {"erro": "agendamos com até 30 dias de antecedência"}
    if local.weekday() not in showroom.dias or not (
        showroom.hora_abertura <= local.hour < showroom.hora_fechamento
    ):
        return {"erro": "o decorado funciona de segunda a sábado, das 9h às 18h"}
    ctx.lead.profile.visita = local
    ctx.changed = True
    return {"ok": True, "visita": local.isoformat(), "endereco": showroom.endereco, **_rescore(ctx)}


def _transferir(args: TransferirParaCorretorArgs, ctx: ToolContext) -> dict[str, Any]:
    ctx.lead.status = LeadStatus.ATENDIMENTO_HUMANO
    ctx.handoff_requested = True
    ctx.changed = True
    return {"ok": True, "mensagem": "Corretor avisado; ele entrará em contato em breve pelo WhatsApp."}


def _resumo(args: RegistrarResumoArgs, ctx: ToolContext) -> dict[str, Any]:
    ctx.lead.summary = args.resumo
    ctx.changed = True
    return {"ok": True}


_HANDLERS: dict[str, Callable[[Any, ToolContext], dict[str, Any]]] = {
    "atualizar_lead": _atualizar_lead,
    "simular_financiamento": _simular,
    "consultar_disponibilidade": _disponibilidade,
    "agendar_visita": _agendar,
    "transferir_para_corretor": _transferir,
    "registrar_resumo": _resumo,
}
