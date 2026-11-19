import { type ChainedCommands, createDocument, Editor, Node } from "@tiptap/core";
import { EditorState } from "@tiptap/pm/state";
import StarterKit from "@tiptap/starter-kit";
import Image from "@tiptap/extension-image";
import { Table, TableKit } from "@tiptap/extension-table";
import Subscript from "@tiptap/extension-subscript";
import Superscript from "@tiptap/extension-superscript";
import DOMPurify from "dompurify";

type Bridge = { ready(): void; changed(token: string, html: string): void };
declare const qt: { webChannelTransport: unknown };
declare const QWebChannel: new (transport: unknown, connect: (channel: { objects: { studio: Bridge } }) => void) => unknown;

let bridge: Bridge;
let token = "";
let assets: Record<string, string> = {};
let mode = "source";
let edited = false;
let richToken = "";
let previewWidth = 390;
let previewStylesheet = "";
let previewTimer: number | undefined;
let previewChapter = "";
let previewRevision = 0;
let previewScroll = 0;
const element = document.querySelector<HTMLElement>("#editor")!;
const toolbar = document.querySelector<HTMLElement>("#toolbar")!;
const tableTools = document.querySelector<HTMLElement>("#table-tools")!;
const linkDialog = document.querySelector<HTMLDialogElement>("#link-dialog")!;
const linkForm = document.querySelector<HTMLFormElement>("#link-form")!;
const linkTitle = document.querySelector<HTMLElement>("#link-title")!;
const linkInput = document.querySelector<HTMLInputElement>("#link-target")!;
const linkError = document.querySelector<HTMLElement>("#link-error")!;
const removeLink = document.querySelector<HTMLButtonElement>("#remove-link")!;
let linkTrigger: HTMLButtonElement | null = null;
let linkChapter = "";
const workspace = document.querySelector<HTMLElement>("#workspace")!;
const editorPane = document.querySelector<HTMLElement>("#editor-pane")!;
const previewPane = document.querySelector<HTMLElement>("#preview-pane")!;
const previewTitle = document.querySelector<HTMLElement>("#preview-title")!;
const previewSize = document.querySelector<HTMLElement>("#preview-size")!;
const previewStage = document.querySelector<HTMLElement>("#preview-stage")!;
const previewSurface = document.querySelector<HTMLElement>("#preview-surface")!;
const preview = document.querySelector<HTMLIFrameElement>("#preview")!;

function isEditing(view: string) {
  return view === "rich" || view === "split";
}

function bodyContent(html: string) {
  const parsed = new DOMParser().parseFromString(html, "text/html");
  return DOMPurify.sanitize(parsed.body.innerHTML, {
    FORBID_TAGS: ["style", "input", "form", "iframe", "video", "audio"],
    FORBID_ATTR: ["srcset"],
  });
}

function assetURL(src: string) {
  return Object.hasOwn(assets, src) ? assets[src] : "";
}

const BookImage = Image.extend({
  addNodeView() {
    return ({ node }) => {
      const img = document.createElement("img");
      img.alt = node.attrs.alt || "";
      const url = assetURL(node.attrs.src);
      if (url) img.src = url;
      return { dom: img };
    };
  },
});

const BookTable = Table.extend({
  parseHTML() {
    return [{
      tag: "table",
      contentElement: (table: HTMLElement) => {
        // ProseMirror stores rows directly. HTML gives them section wrappers.
        const rows = document.createElement("div");
        for (const row of (table as HTMLTableElement).rows) rows.append(row.cloneNode(true));
        return rows;
      },
    }];
  },
  renderHTML({ node, HTMLAttributes }) {
    let sized = false;
    node.firstChild?.forEach(cell => { sized ||= Boolean(cell.attrs.colwidth?.some(Boolean)); });
    if (sized) return this.parent!({ node, HTMLAttributes });
    return ["table", HTMLAttributes, ["tbody", 0]];
  },
});

const bookBlocks = ["main", "section", "article", "footer", "div", "figure", "figcaption"].map(tag =>
  Node.create({
    name: "book_" + tag,
    group: "block",
    content: tag === "figcaption" ? "inline*" : "block+",
    parseHTML: () => [{ tag }],
    renderHTML: () => [tag, 0],
  }),
);

const editor = new Editor({
  element,
  extensions: [
    StarterKit.configure({ link: { openOnClick: false } }),
    BookImage, TableKit.configure({ table: false }), BookTable, Subscript, Superscript, ...bookBlocks,
  ],
  editorProps: {
    attributes: {
      "aria-label": "章节正文",
      "aria-placeholder": "输入或粘贴章节正文…",
      role: "textbox",
      "aria-multiline": "true",
    },
    handleKeyDown: (_view, event) => (
      !event.isComposing && event.ctrlKey && !event.metaKey && !event.altKey && !event.shiftKey &&
      event.key.toLowerCase() === "k" && openLink()
    ),
  },
  onUpdate: ({ editor }) => {
    if (!token || !isEditing(mode)) return;
    edited = true;
    const html = editor.getHTML();
    bridge.changed(token, html);
    if (mode === "split") {
      window.clearTimeout(previewTimer);
      previewTimer = window.setTimeout(() => showPreview(html), 180);
    }
  },
  onTransaction: () => {
    queueMicrotask(updateToolbar);
  },
});

const commands: Record<string, (chain: ChainedCommands) => ChainedCommands> = {
  paragraph: chain => chain.setParagraph(),
  h2: chain => chain.toggleHeading({ level: 2 }),
  h3: chain => chain.toggleHeading({ level: 3 }),
  bold: chain => chain.toggleBold(),
  italic: chain => chain.toggleItalic(),
  sup: chain => chain.toggleSuperscript(),
  sub: chain => chain.toggleSubscript(),
  bullet: chain => chain.toggleBulletList(),
  ordered: chain => chain.toggleOrderedList(),
  quote: chain => chain.toggleBlockquote(),
  table: chain => chain.insertTable({ rows: 3, cols: 2, withHeaderRow: true }),
  undo: chain => chain.undo(),
  redo: chain => chain.redo(),
  "add-row": chain => chain.addRowAfter(),
  "add-column": chain => chain.addColumnAfter(),
  "delete-row": chain => chain.deleteRow(),
  "delete-column": chain => chain.deleteColumn(),
  "merge-cells": chain => chain.mergeCells(),
  "split-cell": chain => chain.splitCell(),
};

function setTabStop(buttons: HTMLButtonElement[], preferred?: HTMLButtonElement) {
  const button = preferred && !preferred.disabled ? preferred : buttons.find(item => !item.disabled);
  for (const item of buttons) item.tabIndex = item === button ? 0 : -1;
  return button;
}

function bindToolbar(container: HTMLElement) {
  const buttons = [...container.querySelectorAll<HTMLButtonElement>("button[data-command]")];
  for (const button of buttons) {
    button.addEventListener("mousedown", event => {
      if (event.button === 0) event.preventDefault();
    });
    button.addEventListener("focus", () => setTabStop(buttons, button));
    button.addEventListener("click", event => {
      const keyboard = event.detail === 0;
      const command = button.dataset.command!;
      if (command === "link") {
        openLink(keyboard ? button : null);
      } else {
        const chain = editor.chain();
        commands[command](keyboard ? chain : chain.focus()).run();
      }
    });
  }
  container.addEventListener("keydown", event => {
    if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
    const available = buttons.filter(button => !button.disabled);
    const index = available.indexOf(event.target as HTMLButtonElement);
    if (index < 0) return;
    let next: number;
    switch (event.key) {
      case "ArrowLeft": next = (index + available.length - 1) % available.length; break;
      case "ArrowRight": next = (index + 1) % available.length; break;
      case "Home": next = 0; break;
      case "End": next = available.length - 1; break;
      default: return;
    }
    event.preventDefault();
    available[next].focus();
  });
  return buttons;
}

const formatButtons = bindToolbar(toolbar);
const tableButtons = bindToolbar(tableTools);
const marks: Record<string, string> = {
  paragraph: "paragraph", bold: "bold", italic: "italic", sub: "subscript", sup: "superscript",
  bullet: "bulletList", ordered: "orderedList", quote: "blockquote",
};
function canEditLink() {
  const { selection } = editor.state;
  return editor.isActive("link") || (!selection.empty && Boolean(editor.state.doc.textBetween(selection.from, selection.to)));
}

function updateToolbar() {
  const focused = document.activeElement as HTMLButtonElement | null;
  editor.view.dom.classList.toggle("is-empty", editor.isEmpty);
  for (const button of formatButtons) {
    const command = button.dataset.command!;
    if (command === "link") {
      button.disabled = !canEditLink();
      button.setAttribute("aria-pressed", String(editor.isActive("link")));
      button.title = button.disabled ? "先选中文字或将光标放入链接" : "插入或编辑链接（Ctrl+K）";
    } else if (command === "h2" || command === "h3") {
      button.setAttribute("aria-pressed", String(editor.isActive("heading", { level: Number(command[1]) })));
    } else if (marks[command]) {
      button.setAttribute("aria-pressed", String(editor.isActive(marks[command])));
    } else {
      button.disabled = !commands[command](editor.can().chain()).run();
    }
  }
  const formatEntry = setTabStop(formatButtons, formatButtons.find(button => button.tabIndex === 0));
  if (focused?.disabled && formatButtons.includes(focused)) formatEntry?.focus();

  const inTable = editor.isActive("table");
  tableTools.hidden = !inTable;
  if (inTable) {
    for (const button of tableButtons) {
      button.disabled = !commands[button.dataset.command!](editor.can().chain()).run();
    }
    const tableEntry = setTabStop(tableButtons, tableButtons.find(button => button.tabIndex === 0));
    if (focused?.disabled && tableButtons.includes(focused)) tableEntry?.focus();
  } else if (focused && tableButtons.includes(focused) && isEditing(mode)) {
    editor.view.focus();
  }
}

function showLinkError(message: string) {
  linkError.textContent = message;
  linkError.hidden = !message;
  if (message) linkInput.setAttribute("aria-invalid", "true");
  else linkInput.removeAttribute("aria-invalid");
}

function openLink(trigger: HTMLButtonElement | null = null) {
  if (!isEditing(mode) || !canEditLink() || linkDialog.open) return false;
  const editing = editor.isActive("link");
  linkTrigger = trigger;
  linkChapter = token;
  linkTitle.textContent = editing ? "编辑链接" : "插入链接";
  linkInput.value = editing ? (editor.getAttributes("link").href || "") : "";
  removeLink.hidden = !editing;
  showLinkError("");
  linkDialog.showModal();
  linkInput.select();
  return true;
}

function validLink(href: string) {
  if (!href || /[\\\s\u0000-\u001F\u007F]/u.test(href)) return false;
  if (/^mailto:/i.test(href)) return /^[^@/?#]+@[^@/?#]+(?:\?.*)?$/u.test(href.slice(7));
  if (/^https?:\/\//i.test(href)) {
    try {
      return Boolean(new URL(href).hostname);
    } catch {
      return false;
    }
  }
  try {
    const path = decodeURIComponent(href.split(/[?#]/, 1)[0]);
    return !path.startsWith("/") && !/[:\\\u0000-\u001F\u007F]/u.test(path);
  } catch {
    return false;
  }
}

linkForm.addEventListener("submit", event => {
  event.preventDefault();
  const href = linkInput.value.trim();
  if (!validLink(href)) {
    showLinkError("请输入有效的 http(s) 网址、mailto: 邮件链接或书内相对路径。");
    linkInput.focus();
    return;
  }
  const existing = editor.isActive("link");
  const attrs = existing ? editor.getAttributes("link") : { target: null, rel: null };
  const chain = editor.chain();
  if (existing) chain.extendMarkRange("link");
  if (chain.setLink({ ...attrs, href }).run()) linkDialog.close();
});

linkInput.addEventListener("input", () => showLinkError(""));
removeLink.addEventListener("click", () => {
  editor.chain().extendMarkRange("link").unsetLink().run();
  linkDialog.close();
});
document.querySelector("#cancel-link")!.addEventListener("click", () => linkDialog.close());
linkDialog.addEventListener("close", () => {
  if (linkChapter !== token || !isEditing(mode)) return;
  if (linkTrigger && !linkTrigger.disabled) linkTrigger.focus();
  else editor.view.focus();
});

function fitPreview() {
  const { clientWidth, clientHeight } = previewStage;
  if (!clientWidth || !clientHeight) return;
  previewPane.tabIndex = workspace.scrollHeight > workspace.clientHeight ? 0 : -1;
  const scale = Math.min(1, clientWidth / previewWidth);
  const viewport = `${previewWidth === 390 ? "手机" : "平板"} · ${previewWidth} px`;
  previewSize.textContent = scale < 1 ? `${viewport} · ${Math.floor(scale * 100)}%` : viewport;
  previewSurface.style.width = `${previewWidth * scale}px`;
  previewSurface.style.height = `${clientHeight}px`;
  preview.style.width = `${previewWidth}px`;
  preview.style.height = `${Math.ceil(clientHeight / scale)}px`;
  preview.style.transform = `scale(${scale})`;
}

new ResizeObserver(fitPreview).observe(previewStage);
previewPane.addEventListener("focus", () => {
  previewPane.scrollIntoView({ block: "nearest" });
});

function previewScrollElement() {
  const page = preview.contentDocument;
  return page?.documentElement.dataset.previewRevision === String(previewRevision)
    ? page.scrollingElement : null;
}

preview.addEventListener("load", () => {
  const scroll = previewScrollElement();
  if (scroll) scroll.scrollTop = previewScroll;
});

function showPreview(html: string) {
  // Keep the previous position until the latest preview document finishes loading.
  previewScroll = previewChapter === token ? previewScrollElement()?.scrollTop ?? previewScroll : 0;
  previewChapter = token;
  const doc = document.implementation.createHTMLDocument("章节预览");
  doc.documentElement.lang = "zh-CN";
  doc.documentElement.dataset.previewRevision = String(++previewRevision);
  const meta = doc.createElement("meta");
  meta.httpEquiv = "Content-Security-Policy";
  meta.content = "default-src 'none'; img-src data:; font-src data:; style-src 'unsafe-inline'";
  doc.head.prepend(meta);
  const css = doc.createElement("style");
  css.textContent = "body{box-sizing:border-box;max-width:" + previewWidth +
    "px;padding:clamp(12px,4vw,24px);margin:0 auto;min-height:100vh;background:white;color:#162a2d;line-height:1.85;" +
    "font-family:serif;overflow-wrap:anywhere}img{max-width:100%;height:auto}article p{text-indent:2em}" +
    "table{border-collapse:collapse;max-width:100%}td,th{border:1px solid #acb6ae;padding:.3em;font-variant-numeric:tabular-nums}" +
    previewStylesheet.replace(/</g, "\\3c ");
  doc.head.append(css);
  doc.body.innerHTML = bodyContent(html);
  for (const img of doc.querySelectorAll("img")) {
    const url = assetURL(img.getAttribute("src") || "");
    if (url) img.src = url;
    else img.removeAttribute("src");
  }
  for (const a of doc.querySelectorAll("a[href]")) {
    if (!a.getAttribute("href")?.startsWith("#")) a.removeAttribute("href");
  }
  preview.srcdoc = "<!doctype html>" + doc.documentElement.outerHTML;
}

Object.assign(window, {
  studioEditor: {
    open(next: string, html: string, resources: Record<string, string>, view: string, stylesheet: string, width: number) {
      window.clearTimeout(previewTimer);
      if (token !== next) edited = false;
      token = next;
      assets = resources;
      mode = view;
      const editing = isEditing(view);
      if (linkDialog.open && (!editing || linkChapter !== next)) linkDialog.close();
      const showingPreview = view === "preview" || view === "split";
      workspace.dataset.view = view;
      editorPane.hidden = !editing;
      element.hidden = !editing;
      toolbar.hidden = !editing;
      if (!editing) tableTools.hidden = true;
      previewPane.hidden = !showingPreview;
      preview.hidden = !showingPreview;
      if (editing) {
        const doc = createDocument(bodyContent(html), editor.schema, {}, { errorOnInvalidContent: true });
        if (richToken !== next || !doc.eq(editor.state.doc)) {
          if (linkDialog.open) linkDialog.close();
          // The editor is shared by chapters. Undo history belongs to one chapter.
          editor.view.updateState(EditorState.create({ doc, plugins: editor.state.plugins }));
          edited = false;
        }
        richToken = next;
        updateToolbar();
      }
      if (showingPreview) {
        previewWidth = width;
        previewStylesheet = stylesheet;
        previewTitle.textContent = view === "split" ? "实时预览" : "阅读预览";
        fitPreview();
        showPreview(view === "split" ? editor.getHTML() : html);
      }
    },
    image(key: string, url: string, alt: string) {
      assets[key] = url;
      editor.chain().focus().setImage({ src: key, alt }).run();
    },
    current() {
      return isEditing(mode) && edited ? { token, html: editor.getHTML() } : null;
    },
  },
});
new QWebChannel(qt.webChannelTransport, ({ objects }) => {
  bridge = objects.studio;
  bridge.ready();
});
