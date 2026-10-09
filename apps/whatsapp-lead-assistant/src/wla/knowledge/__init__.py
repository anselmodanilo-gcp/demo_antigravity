"""Base de conhecimento do empreendimento (dados fictícios, versionados com o código)."""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources

from pydantic import BaseModel, ConfigDict


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Unit(_Frozen):
    codigo: str
    nome: str
    area_m2: int
    preco_a_partir: int
    disponiveis: int
    vagas: int
    elegivel_mcmv: bool


class FinancingRules(_Frozen):
    percentual_financiado_padrao: float
    prazo_meses_padrao: int
    comprometimento_maximo_renda: float
    taxas_anuais: dict[str, float]
    mcmv_renda_maxima: int
    entrada_parcelas_max: int
    aceita_fgts: bool


class Showroom(_Frozen):
    endereco: str
    dias: list[int]  # 0 = segunda ... 6 = domingo
    hora_abertura: int
    hora_fechamento: int


class Knowledge(_Frozen):
    empreendimento: str
    incorporadora: str
    cidade: str
    bairro: str
    entrega_prevista: str
    unidades: list[Unit]
    financiamento: FinancingRules
    decorado: Showroom
    texto: str = ""

    def unit(self, codigo: str | None) -> Unit | None:
        return next((u for u in self.unidades if u.codigo == codigo), None)

    @property
    def cheapest(self) -> Unit:
        return min(self.unidades, key=lambda u: u.preco_a_partir)


@lru_cache(maxsize=1)
def load_knowledge() -> Knowledge:
    pkg = resources.files("wla.knowledge")
    data = json.loads((pkg / "residencial_jardins.json").read_text(encoding="utf-8"))
    data["texto"] = (pkg / "residencial_jardins.md").read_text(encoding="utf-8")
    return Knowledge.model_validate(data)
