# ADR-0002 — Contagem Oracle via Worker do site (refina o ADR-0001)

- **Status**: Aceito
- **Data**: 2026-10-12
- **Relação**: **Supersede parcial do ADR-0001** (apenas a fonte das certificações Oracle;
  o restante do ADR-0001 permanece válido). RF-08.

## Contexto
O ADR-0001 decidiu que a GitHub Action lê a **API pública do Credly direto** e **não
depende do Worker** do site (`-` consequência: "Autônomo: não depende do Worker/infra do
site"). Isso vale para as certificações **AWS**, que estão no feed público do Credly
(`https://www.credly.com/users/<user>/badges.json`, filtradas por
`badge_template.type_category == "Certification"`).

As certificações **Oracle**, porém, **não existem no feed público do Credly**: a Oracle
emite em plataforma própria e, no ecossistema Credly, aparecem como *external badges*
(`EmulatedBadge`), que o feed `badges.json` não retorna. Não há, hoje, endpoint público do
Credly que liste as Oracle do usuário. A única superfície programática que as expõe é o
**Worker do site** (`https://matos.dev.br/api/credly/external`), que proxia esses external
badges com shape `{ data: [ { external_badge: { badge_name, issued_at_date, badge_url,
image_url } } ], metadata: { total_count, next_page_url } }`.

## Decisão
A Action passa a obter a **contagem Oracle do Worker**, derivada de `metadata.total_count`
(hoje = 4), e **mantém a AWS vindo direto do `badges.json`** do Credly. Ou seja:

- **AWS** → Credly público, direto (inalterado pelo ADR-0001).
- **Oracle** → Worker do site (`/api/credly/external`), contagem via `metadata.total_count`.

A URL do Worker é configurável por variável de repo (`ORACLE_URL`), com default para o
endpoint de produção. Nada de contagem Oracle hardcoded no script.

## Consequências
- (+) Oracle deixa de ser um número estático no código; passa a derivar de uma fonte real,
  consistente com o site.
- (+) AWS continua autônomo em relação ao Worker (só a parte Oracle depende dele).
- (−) **Acoplamento parcial ao Worker**: isto refina o ADR-0001, que previa independência
  total do Worker. A dependência fica restrita à contagem Oracle.
- (−) Se o Worker cair (rede/HTTP/JSON inválido), o job **falha safe**: `exit 1`, **sem
  commit** — mesma política de erro aplicada à falha do Credly. O README nunca recebe bloco
  parcial ou zerado.

## Alternativas consideradas
- **Manter Oracle hardcoded no script** (como no rascunho): simples, mas desatualiza a cada
  nova cert Oracle e contraria o princípio de "zero número fixo" do RF-08 — rejeitado.
- **Esperar um endpoint Credly que liste external badges**: não existe publicamente hoje —
  inviável.
- **Imagens Oracle locais em `/assets` + lista manual**: vira manutenção manual e lista
  nomeada, contrariando D3 (badges por provedor) — rejeitado.

## Notas
- Revisitar se/quando a Oracle (ou o Credly) oferecer uma fonte pública das Oracle: nesse
  caso, a Action poderia voltar à independência total prevista no ADR-0001 e este ADR seria
  marcado como superado.
