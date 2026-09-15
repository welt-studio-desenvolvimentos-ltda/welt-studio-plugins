// comfyui-welt-live — carrega no canvas aberto o grafo que o agente editou.
//
// A guarda central: aplicar substitui o canvas inteiro. Se a pessoa mexeu
// no grafo e não salvou, recarregar apaga esse trabalho sem aviso. Então
// nunca carregamos por cima de alteração que não conhecemos.
//
// E a recusa é silenciosa de propósito: nada aqui pede confirmação à pessoa.
// Um canvas divergente significa que o agente editou sobre um estado velho,
// e é ele quem conserta — relendo com comfy_read_canvas e publicando de
// novo. Um botão de "aplicar mesmo assim" transferiria para ela a decisão
// sobre um conflito que ela não criou.

import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const EVENTO = "welt.graph";
const EVENTO_PULL = "welt.pull";
const CHAVE_AUTO = "welt-live.auto";

let pendente = null;        // grafo em carregamento, para a fila não o descartar
let ultimaVersao = 0;
let ultimaAssinatura = null; // como o canvas ficou depois do nosso último carregamento
let aplicando = null;        // promessa do carregamento em curso
let alinhouNoBoot = false;   // o alinhamento de abertura já rodou?

/** Auto-aplicar está ligado? Padrão sim; desliga nas configurações. */
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
 * A assinatura é registrada duas vezes, e as duas importam.
 *
 * Na hora, ainda dentro do `try`: a partir do instante em que o canvas
 * muda, `canvasSujo()` precisa comparar contra o resultado da NOSSA edição.
 * Sem esta escrita, a próxima publicação — que numa sequência de edições
 * chega antes do quadro seguinte — se compararia com o estado anterior à
 * troca e seria recusada como se a pessoa tivesse mexido.
 *
 * E de novo depois de um quadro, porque o grafo ainda assenta logo após a
 * troca; a segunda medida é a que vale dali em diante. Entre as duas há uma
 * janela de um quadro em que uma publicação pode ser recusada à toa — erro
 * para o lado de não apagar trabalho, que é o único aceitável aqui.
 *
 * Usa `graph.configure`, e NÃO `loadGraphData`. O quarto argumento deste
 * último é a identidade do workflow, e o padrão dele — `null` — significa
 * "workflow novo": cada publicação abria mais uma aba, primeiro com o nome
 * do arquivo de trabalho do agente, depois como "Unsaved Workflow (2)",
 * "(3)". Passar o workflow ativo em vez de null exigiria alcançar a store
 * interna do frontend, que não é API de extensão.
 *
 * `graph.configure` é litegraph puro: troca o conteúdo do grafo que já está
 * aberto, sem tocar em aba nem em identidade. É o que espelhar quer dizer.
 * Em troca, não roda o pós-processamento do ComfyUI (aviso de node ausente,
 * migração de reroute) — aceitável aqui, porque o grafo que chega saiu desta
 * mesma instalação e passou pelo catálogo do comfy-cli.
 */
async function aplicar(alvo) {
  if (!(await esperarPronto())) return;
  try {
    app.graph.configure(alvo.graph);
    app.graph.setDirtyCanvas(true, true);
    ultimaAssinatura = assinatura();
  } catch (erro) {
    // Uma falha nossa não pode derrubar o ComfyUI da pessoa: registra e
    // desiste. A próxima publicação do agente tenta de novo.
    console.error("[welt-live] não consegui aplicar o grafo:", erro);
    return;
  }
  await proximoQuadro();
  ultimaAssinatura = assinatura();
  if (pendente === alvo) pendente = null;
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
      if (alvo) await aplicar(alvo);
    });
  return aplicando;
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

      // Os dois motivos de não aplicar são distintos e não têm a mesma
      // saída — dizer "o canvas mudou" quando o que houve foi a pessoa
      // desligar o automático manda o agente para um conserto que nunca
      // funciona: ele releria e republicaria em laço, e o desligado
      // continuaria desligado.
      if (!autoLigado()) {
        console.warn(
          "[welt-live] publicação não aplicada: 'Aplicar edições automaticamente' " +
            "está desligado nas configurações do ComfyUI, em Welt Live. Não há " +
            "aplicação manual — religue a opção para o canvas voltar a acompanhar."
        );
        return;
      }
      if (!canvasSujo()) {
        enfileirar(dados);
        return;
      }
      // O canvas mudou desde a última vez que o conhecemos, então esta
      // publicação foi montada sobre um estado velho e aplicá-la apagaria o
      // que a pessoa fez. Recusamos — e não perguntamos nada a ela.
      //
      // Quem resolve é o agente: ele relê com comfy_read_canvas, refaz a
      // edição sobre o estado atual e publica de novo, e aí a assinatura
      // bate e entra sozinha. Pedir um clique aqui seria transferir para a
      // pessoa um conflito que não é dela.
      console.warn(
        "[welt-live] publicação recusada: o canvas mudou desde a última leitura. " +
          "O agente precisa reler com comfy_read_canvas antes de editar."
      );
    });

  },

  /**
   * Alinha uma aba que abriu depois de o agente já ter editado.
   *
   * Aqui, e não no setup: no setup o canvas ainda não existe, e carregar um
   * grafo ali aborta a inicialização do ComfyUI. Este hook roda depois de o
   * app ter configurado o próprio grafo, que é o primeiro momento seguro.
   *
   * Só na primeira vez. Nossas aplicações passam por `graph.configure`, que
   * não dispara hook de extensão, mas este aqui dispara sempre que a pessoa
   * abre outro workflow — e refazer a linha de base ali desarmaria a guarda
   * justamente quando o agente está com o grafo anterior na mão.
   */
  async afterConfigureGraph() {
    if (alinhouNoBoot) return;
    alinhouNoBoot = true;

    // O que está na tela agora vira a linha de base, e nada é carregado por
    // cima. Dois motivos:
    //
    // Não há o que proteger. A página acabou de carregar, e um reload já
    // levou junto qualquer alteração não salva — o que o ComfyUI restaurou é
    // estado persistido dele. Tratar isso como "trabalho em risco" fazia toda
    // abertura recusar a primeira publicação do agente sem motivo.
    //
    // E não há o que impor. A publicação guardada pode ser de horas atrás, de
    // outro workflow; aplicá-la por cima do que a pessoa acabou de abrir é
    // justamente o susto que esta extensão existe para evitar. Quando o
    // agente precisar do que está aqui, ele lê com comfy_read_canvas.
    //
    // Com isso a recusa passa a acontecer só no caso que a justifica: a
    // pessoa mexeu no canvas depois da última vez que nós o conhecemos.
    ultimaAssinatura = assinatura();
    try {
      const r = await api.fetchApi("/welt-live/state");
      const estado = await r.json();
      // Só acompanha o contador, para não reaplicar uma publicação anterior
      // quando a próxima chegar pelo websocket.
      if (estado?.version > 0) ultimaVersao = estado.version;
    } catch {
      // Sem o contador a próxima publicação chega igual; nada a fazer.
    }
  },

});
