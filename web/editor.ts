import { createDocument, Editor, Node } from "@tiptap/core";
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
  editorProps: { attributes: {
    "aria-label": "章节正文",
    "aria-placeholder": "输入或粘贴章节正文…",
    role: "textbox",
    "aria-multiline": "true",
  } },
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

const commands: Record<string, () => boolean> = {
  paragraph: () => editor.chain().focus().setParagraph().run(),
  h2: () => editor.chain().focus().toggleHeading({ level: 2 }).run(),
  h3: () => editor.chain().focus().toggleHeading({ level: 3 }).run(),
  bold: () => editor.chain().focus().toggleBold().run(),
  italic: () => editor.chain().focus().toggleItalic().run(),
  sup: () => editor.chain().focus().toggleSuperscript().run(),
  sub: () => editor.chain().focus().toggleSubscript().run(),
  bullet: () => editor.chain().focus().toggleBulletList().run(),
  ordered: () => editor.chain().focus().toggleOrderedList().run(),
  quote: () => editor.chain().focus().toggleBlockquote().run(),
  table: () => editor.chain().focus().insertTable({ rows: 3, cols: 2, withHeaderRow: true }).run(),
  undo: () => editor.chain().focus().undo().run(),
  redo: () => editor.chain().focus().redo().run(),
};
const buttons = [...toolbar.querySelectorAll<HTMLButtonElement>("button")];
for (const button of buttons) button.addEventListener("click", () => commands[button.dataset.command!]());
const marks: Record<string, string> = {
  paragraph: "paragraph", bold: "bold", italic: "italic", sub: "subscript", sup: "superscript",
  bullet: "bulletList", ordered: "orderedList", quote: "blockquote",
};
function updateToolbar() {
  editor.view.dom.classList.toggle("is-empty", editor.isEmpty);
  for (const button of buttons) {
    const command = button.dataset.command!;
    if (command === "undo") {
      button.disabled = !editor.can().undo();
    } else if (command === "redo") {
      button.disabled = !editor.can().redo();
    } else if (command === "h2" || command === "h3") {
      button.setAttribute("aria-pressed", String(editor.isActive("heading", { level: Number(command[1]) })));
    } else if (marks[command]) {
      button.setAttribute("aria-pressed", String(editor.isActive(marks[command])));
    }
  }
}

function fitPreview() {
  const { clientWidth, clientHeight } = previewStage;
  if (!clientWidth || !clientHeight) return;
  const scale = Math.min(1, clientWidth / previewWidth);
  previewSurface.style.width = `${previewWidth * scale}px`;
  previewSurface.style.height = `${clientHeight}px`;
  preview.style.width = `${previewWidth}px`;
  preview.style.height = `${Math.ceil(clientHeight / scale)}px`;
  preview.style.transform = `scale(${scale})`;
}

new ResizeObserver(fitPreview).observe(previewStage);

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
  meta.content = "default-src 'none'; img-src data:; style-src 'unsafe-inline'";
  doc.head.prepend(meta);
  const css = doc.createElement("style");
  css.textContent = "body{box-sizing:border-box;max-width:" + previewWidth +
    "px;padding:clamp(12px,4vw,24px);margin:0 auto;min-height:100vh;background:white;color:#182236;line-height:1.85;" +
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
      const showingPreview = view === "preview" || view === "split";
      workspace.dataset.view = view;
      editorPane.hidden = !editing;
      element.hidden = !editing;
      toolbar.hidden = !editing;
      previewPane.hidden = !showingPreview;
      preview.hidden = !showingPreview;
      if (editing) {
        const doc = createDocument(bodyContent(html), editor.schema, {}, { errorOnInvalidContent: true });
        if (richToken !== next || !doc.eq(editor.state.doc)) {
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
        previewSize.textContent = `${width === 390 ? "手机" : "平板"} · ${width} px`;
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
