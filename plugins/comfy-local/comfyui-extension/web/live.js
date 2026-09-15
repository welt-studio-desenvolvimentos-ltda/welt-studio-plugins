// comfyui-welt-live — carrega no canvas aberto o grafo que o agente editou.
//
// A guarda central: app.loadGraphData substitui o canvas inteiro. Se a
// pessoa mexeu no grafo e não salvou, recarregar apaga esse trabalho sem
// aviso. Então o padrão é nunca recarregar por cima de alteração pendente:
// o grafo fica guardado e um botão aparece, e a pessoa decide.

import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const EVENTO = "welt.graph";
const EVENTO_PULL = "welt.pull";
const CHAVE_AUTO = "welt-live.auto";

let pendente = null;        // grafo recebido que ainda não foi aplicado
let ultimaVersao = 0;
let ultimaAssinatura = null; // como o canvas ficou depois do nosso último carregamento
let aplicando = null;        // promessa do carregamento em curso
let alinhouNoBoot = false;   // o alinhamento de abertura já rodou?

/** Auto-aplicar está ligado? Padrão sim; a pessoa desliga pelo botão. */
function autoLigado() {
  try {
    return localStorage.getItem(CHAVE_AUTO) !== "off";
  } catch {
    // Janela anônima ou storage bloqueado: segue no padrão em memória.
    return true;
  }
}

function definirAuto(ligado) {
  try {
    localStorage.setItem(CHAVE_AUTO, ligado ? "on" : "off");
  } catch {
    /* sem storage, vale só nesta aba */
  }
}

/**
 * Assinatura ESTRUTURAL do grafo: o que existe e o que está ligado em quê.
 *
 * Deliberadamente não perguntamos ao ComfyUI se o workflow está "sujo": o
 * caminho para isso vive numa store interna (`workflowStore.activeWorkflow`)
 * que não é API de extensão e muda de lugar entre versões do frontend.
 *
 * E deliberadamente não usamos `serialize()` inteiro: ele carrega posição,
 * tamanho e estado de visualização, que o próprio ComfyUI ajusta depois de
 * carregar. Comparar isso marcaria como "mexido pela pessoa" um canvas em
 * que ninguém tocou — que foi exatamente o que aconteceu no primeiro teste.
 */
function assinatura() {
  try {
    const g = app.graph?.serialize();
    if (!g) return null;
    const nodes = (g.nodes ?? [])
      .map((n) => [n.id, n.type, JSON.stringify(n.widgets_values ?? [])].join("|"))
      .sort();
    const links = (g.links ?? []).map((l) => l.slice(1, 5).join("|")).sort();
    return JSON.stringify({ nodes, links });
  } catch {
    return null;
  }
}

/**
 * O canvas tem alteração que se perderia num recarregamento?
 *
 * Sujo = mudou desde a última vez que nós mesmos carregamos. Um canvas que
 * nunca recebeu nada nosso só é limpo se estiver vazio. Na dúvida — sem
 * conseguir serializar — tratamos como sujo: errar para o lado de não apagar
 * o trabalho de alguém é o único erro aceitável aqui.
 */
function canvasSujo() {
  const agora = assinatura();
  if (agora === null) return true;
  if (ultimaAssinatura !== null) return agora !== ultimaAssinatura;
  return (app.graph?._nodes?.length ?? 0) > 0;
}

/**
 * Nome do workflow aberto, quando dá para descobrir.
 *
 * É informativo — serve para o agente dizer "editei o seu image_flux2" em
 * vez de "editei o grafo". Não há API de extensão para isto, então tentamos
 * o caminho conhecido e desistimos em silêncio: um nome ausente não impede
 * nada.
 */
function nomeDoWorkflow() {
  try {
    return app.graph?.extra?.workflow_name ?? null;
  } catch {
    return null;
  }
}

/** Espera o navegador desenhar, para a assinatura sair já assentada. */
function proximoQuadro() {
  return new Promise((r) => requestAnimationFrame(() => setTimeout(r, 0)));
}

/**
 * O canvas já existe?
 *
 * Carregar um grafo antes disso aborta a inicialização do ComfyUI inteiro
 * com "getCanvas: canvas is null" — e o diálogo de erro aponta o dedo para
 * esta extensão, com razão. Nada aqui pode rodar antes de a tela existir.
 */
function pronto() {
  return Boolean(app.canvas && app.graph);
}

/** Aguarda o canvas aparecer, desistindo em silêncio se demorar demais. */
async function esperarPronto(tetoMs = 15000) {
  const limite = Date.now() + tetoMs;
  while (!pronto()) {
    if (Date.now() > limite) return false;
    await proximoQuadro();
  }
  return true;
}

/**
 * Carrega o grafo e registra como o canvas ficou.
 *
 * `loadGraphData` é assíncrona — todo o frontend a chama com await. Sem
 * esperar, a assinatura sairia do estado anterior e a edição seguinte
 * pareceria alteração da pessoa.
 */
async function aplicar(alvo) {
  if (!(await esperarPronto())) return;
  try {
    await app.loadGraphData(alvo.graph, true, true, alvo.name ?? null);
  } catch (erro) {
    // Uma falha nossa não pode derrubar o ComfyUI da pessoa. O grafo fica
    // pendente e o botão aparece, para ela tentar quando quiser.
    console.error("[welt-live] não consegui carregar o grafo:", erro);
    atualizarBotao();
    return;
  }
  await proximoQuadro();
  ultimaAssinatura = assinatura();
  // Só limpa o que acabamos de aplicar. Uma edição que chegou enquanto este
  // carregamento acontecia já trocou `pendente` por um grafo mais novo, e
  // zerar aqui a descartaria em silêncio.
  if (pendente === alvo) pendente = null;
  atualizarBotao();
}

/**
 * Enfileira um carregamento. Numa sequência de edições os eventos chegam
 * mais rápido do que o canvas carrega, e só o último grafo importa — os
 * intermediários já foram superados quando chegam a vez deles.
 */
function enfileirar(dados) {
  pendente = dados;
  const anterior = aplicando ?? Promise.resolve();
  aplicando = anterior
    .catch(() => {})
    .then(async () => {
      const alvo = pendente;
      if (!alvo) return;
      await aplicar(alvo);
    });
  return aplicando;
}

function atualizarBotao() {
  const botao = document.getElementById("welt-live-aplicar");
  if (!botao) return;
  botao.hidden = pendente === null;
  botao.textContent = pendente ? `Aplicar edição do agente (v${pendente.version})` : "";
}

function montarBotao() {
  const botao = document.createElement("button");
  botao.id = "welt-live-aplicar";
  botao.hidden = true;
  botao.style.cssText = [
    "position:fixed", "right:16px", "z-index:1000",
    "top:calc(16px + env(safe-area-inset-top, 0px))",
    "padding:8px 14px", "border-radius:8px", "border:1px solid #888",
    "background:#2d6cdf", "color:#fff", "font:600 13px system-ui,sans-serif",
    "cursor:pointer", "box-shadow:0 2px 8px rgba(0,0,0,.3)",
  ].join(";");
  botao.addEventListener("click", () => {
    if (pendente) enfileirar(pendente);
  });
  document.body.appendChild(botao);
}

app.registerExtension({
  name: "welt.live",

  // Deixa a pessoa desligar o auto-aplicar sem sair do ComfyUI. `settings` é
  // o campo que a interface ComfyExtension expõe para isto — um hook com
  // outro nome seria ignorado em silêncio e o toggle nunca apareceria.
  settings: [
    {
      id: "welt.live.auto",
      // A categoria sai do id por padrão ("welt" › "live"); fixamos aqui para
      // a seção aparecer como "Welt Live", que é onde o README manda procurar.
      category: ["Welt Live", "Canvas"],
      name: "Aplicar edições automaticamente",
      type: "boolean",
      defaultValue: true,
      onChange: (valor) => definirAuto(valor),
    },
  ],

  async setup() {
    montarBotao();

    // O agente pediu o grafo que está no canvas agora.
    //
    // Se a resposta DESTA aba for a que o servidor aceitou, registramos a
    // assinatura como se nós mesmos tivéssemos carregado: o agente passa a
    // conhecer este estado, então a edição que ele devolver em cima dele não
    // é sobrescrita indevida. Se a pessoa mexer no canvas entre o pedido e a
    // volta, a assinatura muda de novo e a guarda volta a segurar.
    //
    // O pedido vai para todas as abas, mas só uma resposta é aproveitada. Por
    // isso esperamos o servidor dizer qual: marcar como conhecida uma aba que
    // perdeu a corrida deixaria o trabalho não salvo dela sem guarda, e a
    // próxima edição do agente o apagaria — o oposto do que esta extensão
    // existe para garantir.
    api.addEventListener(EVENTO_PULL, async (evento) => {
      const token = evento.detail?.token;
      if (!token) return;
      let grafo = null;
      let assinaturaEnviada = null;
      try {
        grafo = app.graph?.serialize() ?? null;
        assinaturaEnviada = assinatura();
      } catch (erro) {
        console.error("[welt-live] não consegui serializar o canvas:", erro);
      }
      try {
        const r = await api.fetchApi("/welt-live/canvas", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ token, graph: grafo, name: nomeDoWorkflow() }),
        });
        const resposta = await r.json();
        if (resposta?.ok && !resposta.ignored && assinaturaEnviada !== null) {
          ultimaAssinatura = assinaturaEnviada;
        }
      } catch (erro) {
        // Sem resposta, a rota do lado servidor expira sozinha e devolve um
        // erro com hint. Nada a fazer aqui além de não derrubar a aba.
        console.error("[welt-live] não consegui responder ao pedido:", erro);
      }
    });

    api.addEventListener(EVENTO, (evento) => {
      const dados = evento.detail;
      // Compara por diferença, e não por "maior que": o contador vive na
      // memória do ComfyUI e recomeça do zero quando ele reinicia. Com `<=`,
      // uma aba que já viu a versão 5 ignoraria em silêncio as cinco
      // primeiras publicações depois do restart.
      if (!dados?.graph || dados.version === ultimaVersao) return;
      ultimaVersao = dados.version;

      if (autoLigado() && !canvasSujo()) {
        enfileirar(dados);
        return;
      }
      // Alteração pendente no canvas: guarda e deixa a pessoa decidir, em
      // vez de sobrescrever o que ela estava fazendo.
      pendente = dados;
      atualizarBotao();
    });

  },

  /**
   * Alinha uma aba que abriu depois de o agente já ter editado.
   *
   * Aqui, e não no setup: no setup o canvas ainda não existe, e carregar um
   * grafo ali aborta a inicialização do ComfyUI. Este hook roda depois de o
   * app ter configurado o próprio grafo, que é o primeiro momento seguro.
   *
   * Só na primeira vez — ele dispara a cada carregamento, inclusive nos
   * nossos, e sem a trava viraria laço.
   */
  async afterConfigureGraph() {
    if (alinhouNoBoot) return;
    alinhouNoBoot = true;
    try {
      const r = await api.fetchApi("/welt-live/state");
      const estado = await r.json();
      if (!estado?.graph || estado.version <= 0) return;
      ultimaVersao = estado.version;
      if (canvasSujo()) {
        pendente = estado;
        atualizarBotao();
        return;
      }
      await enfileirar(estado);
    } catch {
      // Não alinhar no boot é inofensivo: a próxima publicação chega pelo
      // websocket de qualquer jeito.
    }
  },

});
