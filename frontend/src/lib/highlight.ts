/**
 * Citation highlight for parsed content.
 *
 * The model cites chunk text, and chunks are slices of the parsed markdown —
 * so the cited lines are found by text search in the rendered markdown, not
 * in the original file renderers (no pdf.js worker, no text-layer fragility,
 * works for scanned PDFs too).
 */

import katex from "katex";

export function escapeRegExp(s: string) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * Strip HTML tags + markdown formatting from chunk text so it matches the
 * plain words in the rendered output (chunks keep `##`, `**`, `<table>` …
 * markup that never appears as literal text).
 */
export function stripMarkup(s: string) {
  return s
    .replace(/<[^>]*>/g, " ") // html tags
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1") // images → alt text
    .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1") // links → link text
    .replace(/(\*\*|__)(.*?)\1/g, "$2") // bold
    .replace(/~~(.*?)~~/g, "$1") // strikethrough
    .replace(/[*`~#>|]/g, " "); // leftover markers
}

export function unwrapMark(m: Element) {
  const parent = m.parentNode;
  if (!parent) return;
  parent.replaceChild(m.ownerDocument.createTextNode(m.textContent ?? ""), m);
}

/** Single-char folds applied before matching (length-preserving). */const FOLDS: Record<string, string> = {
  "‘": "'",
  "’": "'",
  "‚": "'",
  "“": '"',
  "”": '"',
  "„": '"',
  "–": "-",
  "—": "-",
  "−": "-",
  " ": " ",
};

/** Fold one char; NFKC handles ligatures (ﬁ→fi) and wide forms. */
function foldChar(ch: string): string {
  return FOLDS[ch] ?? ch.normalize("NFKC");
}

/**
 * Lowercase + fold, keeping a map from each normalized char back to its
 * original index so match offsets can be wrapped exactly.
 */
export function normWithMap(s: string): { text: string; map: number[] } {
  let lower = "";
  const lowerMap: number[] = [];
  for (let i = 0; i < s.length; i += 1) {
    const c = s[i].toLowerCase();
    lower += c;
    for (let j = 0; j < c.length; j += 1) lowerMap.push(i);
  }
  let text = "";
  const map: number[] = [];
  for (let i = 0; i < lower.length; i += 1) {
    const f = foldChar(lower[i]);
    for (let j = 0; j < f.length; j += 1) {
      text += f[j];
      map.push(lowerMap[i]);
    }
  }
  // Collapse whitespace runs (mapped to the first space's origin).
  let out = "";
  const outMap: number[] = [];
  let inSpace = false;
  for (let i = 0; i < text.length; i += 1) {
    const isSpace = /\s/.test(text[i]);
    if (isSpace && (inSpace || out.length === 0)) continue;
    inSpace = isSpace;
    out += isSpace ? " " : text[i];
    outMap.push(map[i]);
  }
  if (out.endsWith(" ")) {
    out = out.slice(0, -1);
    outMap.pop();
  }
  return { text: out, map: outMap };
}

/**
 * Find `snippet` in `root`'s text nodes and wrap it in <mark> spans.
 * Both sides are lowercased + folded (ligatures, quotes, dashes) so parser
 * text aligns with rendered text; tries contiguous and space-joined text,
 * shrinking from the leading 20 words down to 4. Returns the first mark.
 */
export function highlightSnippet(
  root: HTMLElement,
  snippet: string,
  markClass: string,
): HTMLElement | null {
  root.querySelectorAll(`mark.${markClass}`).forEach(unwrapMark);
  root.normalize();
  const clean = stripMarkup(snippet);
  const needleWords = normWithMap(clean).text.split(" ").filter(Boolean);
  if (needleWords.length < 3) return null;
  const nodes: Text[] = [];
  const walker = root.ownerDocument.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  let n: Node | null;
  while ((n = walker.nextNode())) {
    const t = n as Text;
    if (t.textContent && t.textContent.trim()) nodes.push(t);
  }
  if (!nodes.length) return null;
  // Normalized haystack per node; nodeRanges maps node text back to raw.
  const normNodes = nodes.map((t) => normWithMap(t.textContent ?? ""));
  for (const joiner of ["", " "]) {
    let raw = "";
    // ranges[i] = [normStart, normEnd, nodeIndex, nodeNormOffsetBase]
    const ranges: Array<[number, number, number, number[]]> = [];
    let pos = 0;
    normNodes.forEach((nn, i) => {
      if (i > 0) {
        raw += joiner;
        pos += joiner.length;
      }
      raw += nn.text;
      ranges.push([pos, pos + nn.text.length, i, nn.map]);
      pos += nn.text.length;
    });
    for (let len = Math.min(needleWords.length, 20); len >= 4; len -= 1) {
      const m = new RegExp(needleWords.slice(0, len).map(escapeRegExp).join("\\s+"), "i").exec(raw);
      if (!m?.[0]) continue;
      wrapRange(nodes, ranges, m.index, m.index + m[0].length, markClass);
      return root.querySelector(`mark.${markClass}`);
    }
  }
  return null;
}

export function wrapRange(
  nodes: Text[],
  ranges: Array<[number, number, number, number[]]>,
  start: number,
  end: number,
  markClass: string,
) {
  const doc = nodes[0]?.ownerDocument;
  if (!doc) return;
  nodes.forEach((node, i) => {
    const [sStart, sEnd, , normMap] = ranges[i];
    if (sEnd <= start || sStart >= end) return;
    // Map normalized offsets back to raw offsets in this node.
    const relA = Math.max(0, start - sStart);
    const relB = Math.min(normMap.length, end - sStart);
    if (relB <= relA || relA >= normMap.length) return;
    const a = normMap[relA];
    const b = relB >= normMap.length ? (node.textContent ?? "").length : normMap[relB];
    if (b <= a) return;
    node.splitText(b); // node=[0,b), tail=[b,…)
    const mid = node.splitText(a); // node=[0,a), mid=[a,b)
    const mark = doc.createElement("mark");
    mark.className = markClass;
    mark.textContent = mid.textContent;
    node.parentNode?.replaceChild(mark, mid);
  });
}

/**
 * Fallback scroll target: the page marker line the parser emitted
 * ("p.5", "p 5", "Page 5"). Guarantees the reader at least lands on the
 * cited page when the line-level match misses.
 */
export function findPageAnchor(root: HTMLElement, page: number): HTMLElement | null {
  const patterns = [
    new RegExp(`^p\\.?\\s*0*${page}$`, "i"),
    new RegExp(`^page\\s+0*${page}$`, "i"),
  ];
  const walker = root.ownerDocument.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  let n: Node | null;
  while ((n = walker.nextNode())) {
    const t = (n as Text).textContent?.trim() ?? "";
    if (!t || t.length > 12) continue;
    if (patterns.some((p) => p.test(t))) {
      const el = (n as Text).parentElement;
      if (el && root.contains(el)) return el;
    }
  }
  return null;
}

/** Raw markdown that must never be split by an inserted <mark> tag
 * (would corrupt tables, code, math, links, headings). Emphasis markers
 * (*_) are safe: wrapping keeps their pairing intact or nests inside it. */
const UNSAFE_MD = /[<>`$\\|~#]/;

const TOK_RE = /[\p{L}\p{N}]+/gu;

/** Folded compare-form of one token for citation matching. */
function foldTok(t: string): string {
  return t.toLowerCase().normalize("NFKC");
}

/** Folded word tokens of `s` (offsets discarded — needle side). */
function tokensOf(s: string): string[] {
  const out: string[] = [];
  TOK_RE.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = TOK_RE.exec(s))) out.push(foldTok(m[0]));
  return out;
}

/** Folded word tokens of `s` with exact original offsets (haystack side). */
function tokensWithOffsets(s: string): Array<{ w: string; start: number; end: number }> {
  const out: Array<{ w: string; start: number; end: number }> = [];
  TOK_RE.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = TOK_RE.exec(s))) out.push({ w: foldTok(m[0]), start: m.index, end: m.index + m[0].length });
  return out;
}

/** Index of the first haystack run equal to `seq`, or -1 (work-capped). */
function findRun(hay: Array<{ w: string }>, seq: string[]): number {
  let tried = 0;
  outer: for (let i = 0; i + seq.length <= hay.length; i++) {
    if (hay[i].w !== seq[0]) continue;
    if (tried++ > 400) return -1;
    for (let j = 1; j < seq.length; j++) {
      if (hay[i + j].w !== seq[j]) continue outer;
    }
    return i;
  }
  return -1;
}

/**
 * Deterministic citation targeting at the markdown-string level.
 *
 * The snippet is searched in the raw markdown (same origin as the chunk, so
 * rendering transforms can't break the match). On a hit inside safe text
 * the span is wrapped in `<mark id="cite-target">`; otherwise a `<span
 * id="cite-target">` is spliced after the cited page's marker line, so the
 * reader always lands on the cited page. Requires rehype-raw downstream to
 * pass the tags through. Returns the (possibly unchanged) markdown.
 */
export function spliceCiteMarkers(
  markdown: string,
  snippet: string | null,
  page: number | null,
): { text: string; hasTarget: boolean } {
  // For page citations the parsed page boundary is authoritative. Snippet text
  // can repeat elsewhere, so it must not pull the reader off the cited page.
  if (page !== null) {
    const marker = new RegExp(`^#{0,6}\\s*(?:p\\.?|page)\\s*0*${page}(\\s|$)`, "i");
    const lines = markdown.split("\n");
    let inFence = false;
    for (let i = 0; i < lines.length; i += 1) {
      if (/^\s*```/.test(lines[i])) {
        inFence = !inFence;
        continue;
      }
      if (!inFence && marker.test(lines[i].trim())) {
        lines.splice(i + 1, 0, '<span id="cite-target"></span>');
        return { text: lines.join("\n"), hasTarget: true };
      }
    }
  }
  if (snippet) {
    // Token-run search: markup/punctuation are token separators on both
    // sides, so `**Introduction**:` aligns with `**introduction**:` and
    // `tree-based` with `tree-based`, while offsets stay exact originals.
    const needle = tokensOf(stripMarkup(snippet));
    if (needle.length >= 3) {
      const hay = tokensWithOffsets(markdown);
      for (let len = Math.min(needle.length, 24); len >= 4; len -= 1) {
        const at = findRun(hay, needle.slice(0, len));
        if (at < 0) continue;
        const a = hay[at].start;
        const b = hay[at + len - 1].end;
        const span = markdown.slice(a, b);
        if (!span.trim() || UNSAFE_MD.test(span)) continue;
        const open = '<mark class="md-cite-mark" id="cite-target">';
        return { text: markdown.slice(0, a) + open + span + "</mark>" + markdown.slice(b), hasTarget: true };
      }
    }
  }
  return { text: markdown, hasTarget: false };
}

/** Render \(…\) / \[…\] math inside raw-HTML tables (remark skips HTML blocks). */
const TABLE_MATH_RE = /\\\((.+?)\\\)|\\\[([\s\S]+?)\\\]/g;

export function enhanceTableMath(root: HTMLElement) {
  const tables = root.querySelectorAll("table");
  if (!tables.length) return;
  tables.forEach((tbl) => {
    const walker = root.ownerDocument.createTreeWalker(tbl, NodeFilter.SHOW_TEXT);
    const nodes: Text[] = [];
    let n: Node | null;
    while ((n = walker.nextNode())) {
      const t = n as Text;
      if (t.textContent && /\\[([]/.test(t.textContent)) nodes.push(t);
    }
    nodes.forEach((t) => {
      const s = t.textContent ?? "";
      TABLE_MATH_RE.lastIndex = 0;
      if (!TABLE_MATH_RE.test(s)) return;
      TABLE_MATH_RE.lastIndex = 0;
      const frag = root.ownerDocument.createDocumentFragment();
      let last = 0;
      let m: RegExpExecArray | null;
      while ((m = TABLE_MATH_RE.exec(s))) {
        if (m.index > last) frag.appendChild(root.ownerDocument.createTextNode(s.slice(last, m.index)));
        const display = m[0].startsWith("\\[");
        const span = root.ownerDocument.createElement("span");
        try {
          span.innerHTML = katex.renderToString(m[1] ?? m[2] ?? "", { displayMode: display, throwOnError: false });
        } catch {
          span.textContent = m[0];
        }
        frag.appendChild(span);
        last = m.index + m[0].length;
      }
      frag.appendChild(root.ownerDocument.createTextNode(s.slice(last)));
      t.parentNode?.replaceChild(frag, t);
    });
  });
}

/** Scroll `el` to the vertical center of its nearest scrollable ancestor. */
export function scrollElIntoView(el: HTMLElement, smooth = true) {
  let p: HTMLElement | null = el.parentElement;
  while (p) {
    const oy = getComputedStyle(p).overflowY;
    if ((oy === "auto" || oy === "scroll") && p.scrollHeight > p.clientHeight + 1) {
      const r = el.getBoundingClientRect();
      const c = p.getBoundingClientRect();
      p.scrollTo({ top: p.scrollTop + (r.top - c.top) - c.height / 2 + r.height / 2, behavior: smooth ? "smooth" : "auto" });
      return;
    }
    p = p.parentElement;
  }
  el.scrollIntoView({ block: "center", behavior: smooth ? "smooth" : "auto" });
}
