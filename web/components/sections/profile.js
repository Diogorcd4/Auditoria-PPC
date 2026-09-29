import { el } from "../utils.js";

const FIELD_LABEL = {
  sector: "Setor",
  empresa: "Empresa",
  publico: "Público",
  descricao_publico: "Descrição do público",
  acao: "Ação pretendida",
  geografia: "Geografia",
  landing_page_url: "URL da landing page",
  pagina_destino: "Página de destino",
  produto_ou_servico: "Produto ou serviço",
  preco: "Preço / gama de preços",
  idade: "Idade",
  perfil: "Perfil",
  nivel_poder_compra: "Poder de compra",
  motivacao: "Motivação",
  objecao: "Objecção principal",
  comportamento_online: "Comportamento online",
  objetivo_conversao: "Objetivo de conversão",
  fonte_dados_clientes: "Fonte de dados (clientes)",
  fonte_dados_subscritores: "Fonte de dados (subscritores)",
  conteudo_a_promover: "Conteúdo a promover",
  objectivo_pos_trafego: "Objectivo pós-tráfego",
  motivacao_clique: "Motivação de clique",
  canais: "Canais",
  business_model: "Modelo de negócio",
};

export function renderProfile(audit, ctx = {}) {
  const section = el("section", { class: "section-block internal-only", id: "sec-perfil" });
  section.append(
    el("div", { class: "section-head" }, [
      el("div", { class: "eyebrow" }, "Perfil e prompts"),
      el("h2", { class: "section-title" }, "O que foi usado para preencher os anúncios"),
      el("p", { class: "section-summary" }, "Visível só na vista interna. Cada campo mostra de onde veio o valor. Edite um valor e regenere para atualizar os anúncios."),
    ])
  );

  const table = el("table", { class: "profile-table" });
  for (const [key, entry] of Object.entries(audit.profile)) {
    if (!entry || typeof entry !== "object" || !("value" in entry)) continue; // e.g. conteudo_forte, a plain boolean
    const input = el("input", { value: entry.value, "data-field": key });
    table.appendChild(
      el("tr", {}, [
        el("td", {}, FIELD_LABEL[key] ?? key),
        el("td", {}, [
          input,
          el("span", { class: `badge origin-badge origin-badge--${entry.origin}`, style: "margin-top:6px;display:inline-block" }, `${entry.origin} · ${Math.round(entry.confidence * 100)}%`),
        ]),
      ])
    );
  }

  const card = el("div", { class: "card" }, [table]);

  const verifiedOffers = audit.profile.verified_offers;
  if (verifiedOffers?.length) {
    const offersCard = el("div", { class: "card", style: "margin-top:20px" }, [
      el("h3", { style: "font-size:0.9rem;margin-bottom:4px" }, "Ofertas verificadas"),
      el("p", { style: "font-size:0.82rem;color:var(--muted);margin-bottom:12px" }, "A única base factual que os anúncios podem usar - qualquer superlativo ou promessa fora desta lista é marcado como \"não verificado no site\"."),
    ]);
    for (const offer of verifiedOffers) {
      offersCard.appendChild(
        el("div", { style: "padding:8px 0;border-top:1px solid var(--border)" }, [
          el("div", { style: "font-weight:600;font-size:0.85rem" }, offer.claim),
          el("div", { style: "font-size:0.8rem;color:var(--muted);margin-top:2px" }, `"${offer.quote}"`),
          el("a", { href: offer.url, target: "_blank", rel: "noopener", style: "font-size:0.78rem" }, offer.url),
        ])
      );
    }
    section.appendChild(offersCard);
  } else if (verifiedOffers) {
    section.appendChild(el("div", { class: "card", style: "margin-top:20px;font-size:0.85rem;color:var(--muted)" }, "Não foi possível extrair nenhuma oferta verificada do site - os anúncios ficam mais conservadores, sem números, prazos ou garantias."));
  }

  if ("conteudo_forte" in audit.profile) {
    card.appendChild(
      el("div", { style: "margin-top:12px;font-size:0.82rem;color:var(--muted)" }, [
        "Conteúdo forte (acrescenta o prompt 02.7 de tráfego): ",
        el("strong", { style: "color:var(--text)" }, audit.profile.conteudo_forte ? "Sim" : "Não"),
      ])
    );
  }
  const regenBtn = el("button", { class: "btn btn--primary", style: "margin-top:16px" }, "Regenerar anúncios com este perfil");
  regenBtn.addEventListener("click", async () => {
    const updatedProfile = {};
    for (const [key, entry] of Object.entries(audit.profile)) {
      if (!entry || typeof entry !== "object" || !("value" in entry)) {
        updatedProfile[key] = entry;
        continue;
      }
      const input = table.querySelector(`input[data-field="${key}"]`);
      const newValue = input ? input.value : entry.value;
      updatedProfile[key] = newValue === entry.value ? entry : { value: newValue, origin: "manual", confidence: 1 };
    }
    regenBtn.disabled = true;
    regenBtn.textContent = "A regenerar…";
    try {
      await ctx.onRegenerateAds?.(updatedProfile);
    } finally {
      regenBtn.disabled = false;
      regenBtn.textContent = "Regenerar anúncios com este perfil";
    }
  });
  card.appendChild(regenBtn);
  section.appendChild(card);

  return section;
}
