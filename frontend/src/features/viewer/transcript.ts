/** Supports older m:ss headings and the ingestion pipeline's precise VTT ranges. */
export function transcriptSegments(markdown: string) {
  const stamp = String.raw`\d+:\d{2}(?::\d{2})?(?:\.\d+)?`;
  const pattern = new RegExp(`^##[ \\t]+(${stamp})(?:[ \\t]+-->[ \\t]+(${stamp}))?[ \\t]*\\r?$`, "gm");
  const headings = [...markdown.matchAll(pattern)];
  const seconds = (value: string) => value.split(":").reduce((total, part) => total * 60 + Number(part), 0);
  return headings.flatMap((heading, index) => {
    const start = seconds(heading[1]);
    const end = heading[2] ? seconds(heading[2]) : null;
    const text = markdown.slice(heading.index! + heading[0].length, headings[index + 1]?.index ?? markdown.length).trim();
    return !Number.isFinite(start) || !text ? [] : [{ start, end: end !== null && end > start ? end : null, text }];
  });
}
