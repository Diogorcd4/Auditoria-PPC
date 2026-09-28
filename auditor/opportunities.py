from __future__ import annotations

from typing import Any, Optional

Opportunity = dict[str, Any]


def _opp(service: str, evidence: str, priority: str, pitch: str, owner_services: list[str]) -> Optional[Opportunity]:
    if service not in owner_services:
        return None
    return {"service": service, "evidence": evidence, "priority": priority, "pitch": pitch}


def derive_tracking_opportunities(tracking: dict, owner_services: list[str]) -> list[Opportunity]:
    """Rule-based opportunities from the tracking report (secção 4.2 do briefing). No LLM
    involved - every rule here is an explicit, auditable if/else over what was detected."""
    out: list[Opportunity] = []

    ga4 = tracking["ga4"]
    ua = tracking["ua"]
    google_ads = tracking["google_ads"]
    meta_pixel = tracking["meta_pixel"]
    microsoft_uet = tracking["microsoft_uet"]
    gtm = tracking["gtm"]
    consent_mode = tracking["consent_mode"]

    if not ga4["detected"]:
        out.append(_opp(
            "Tracking e Analytics",
            "Nenhuma tag do Google Analytics 4 detetada no site.",
            "Alta",
            "Instalar e configurar o GA4 é o primeiro passo antes de qualquer campanha paga: sem ele não há dados de comportamento nem de conversão.",
            owner_services,
        ))
    elif not tracking.get("ecommerce_events_in_datalayer"):
        out.append(_opp(
            "Tracking e Analytics",
            "O GA4 está instalado mas não foram observados eventos de conversão (ex. generate_lead, purchase) no dataLayer.",
            "Média",
            "Configurar eventos de conversão no GA4 para que as campanhas tenham dados fiáveis para otimizar.",
            owner_services,
        ))

    if ua["detected"] and not ga4["detected"]:
        out.append(_opp(
            "Tracking e Analytics",
            "Só foi detetada a Universal Analytics, obsoleta e sem recolha de dados desde 2023.",
            "Alta",
            "Migrar para o GA4 com urgência: a Universal Analytics já não recolhe dados.",
            owner_services,
        ))

    if not google_ads["detected"]:
        out.append(_opp(
            "Google Ads",
            "Nenhuma tag do Google Ads (AW-XXXX) nem pedido de conversão detetado no site.",
            "Alta",
            "Instalar a tag de conversões do Google Ads antes de qualquer campanha, para medir leads e permitir remarketing.",
            owner_services,
        ))

    if not meta_pixel["detected"]:
        out.append(_opp(
            "Meta Ads",
            "Nenhum Meta Pixel detetado no site.",
            "Alta",
            "Instalar o Meta Pixel e configurar os eventos de conversão é essencial para qualquer campanha de Meta Ads.",
            owner_services,
        ))
    elif meta_pixel["state"] in ("No código, sem disparo observado", "Não detetado"):
        out.append(_opp(
            "Meta Ads",
            "O Meta Pixel está no código mas não foi observado nenhum evento (ex. Lead, Purchase) a disparar.",
            "Alta",
            "Configurar os eventos de conversão no Pixel para que as campanhas de Meta Ads tenham dados desde o primeiro dia.",
            owner_services,
        ))

    if not microsoft_uet["detected"]:
        out.append(_opp(
            "Microsoft Advertising",
            "Nenhuma tag UET da Microsoft Advertising detetada no site.",
            "Média",
            "Instalar a tag UET permite testar o Microsoft Advertising com o mesmo tracking base do Google Ads.",
            owner_services,
        ))

    if not consent_mode["detected"]:
        out.append(_opp(
            "Tracking e Analytics",
            "Consent Mode v2 não detetado.",
            "Alta",
            "Implementar o Consent Mode v2 é especialmente relevante para campanhas dirigidas a audiências no EEE.",
            owner_services,
        ))

    fires_before_consent = any(tracking[key]["state"] == "A disparar sem consentimento" for key in ("ga4", "ua", "google_ads", "meta_pixel", "microsoft_uet"))
    if fires_before_consent:
        out.append(_opp(
            "Tracking e Analytics",
            "Pelo menos uma tag dispara antes de qualquer escolha de consentimento do visitante.",
            "Média",
            "Validar com um jurista se o disparo antes do consentimento está em conformidade com o RGPD.",
            owner_services,
        ))

    if not gtm["detected"]:
        out.append(_opp(
            "Tracking e Analytics",
            "Nenhum contentor do Google Tag Manager detetado no site.",
            "Média",
            "Implementar um contentor GTM simplifica a gestão de todas as tags futuras sem depender de alterações ao código do site.",
            owner_services,
        ))

    return [o for o in out if o is not None]


def derive_gap_opportunities(global_gaps: list[str], owner_services: list[str], *, max_items: int = 2) -> list[Opportunity]:
    """A couple of "Copy e conversão" opportunities straight from the site-level synthesis'
    global gaps, so opportunities also reflect what the communication analysis found."""
    out: list[Opportunity] = []
    for gap in global_gaps[:max_items]:
        opp = _opp(
            "Copy e conversão",
            gap,
            "Média",
            "Resolver esta lacuna reduz fricção na conversão sem exigir qualquer alteração ao investimento em anúncios.",
            owner_services,
        )
        if opp is not None:
            out.append(opp)
    return out


def derive_opportunities(tracking: dict, global_gaps: list[str], owner_services: list[str]) -> list[Opportunity]:
    return derive_tracking_opportunities(tracking, owner_services) + derive_gap_opportunities(global_gaps, owner_services)
