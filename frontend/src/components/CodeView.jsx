import { useMemo } from "react";

function escapeHtml(text) {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function highlightXml(escaped) {
  // comments first so their inner < > are not treated as tags
  const withComments = escaped.replace(
    /(&lt;!--[\s\S]*?--&gt;)/g,
    '<span class="xml-comment">$1</span>',
  );

  return withComments.replace(
    /(&lt;\/?)([a-zA-Z_][\w.-]*(?::[a-zA-Z_][\w.-]*)?)([\s\S]*?)(\/?&gt;)/g,
    (match, open, name, attrs, close) => {
      if (match.includes('class="xml-comment"')) return match;
      const highlightedAttrs = attrs
        .replace(
          /([a-zA-Z_][\w.-]*(?::[a-zA-Z_][\w.-]*)?)(=)(&quot;[\s\S]*?&quot;|&#39;[\s\S]*?&#39;|"[\s\S]*?"|'[\s\S]*?')/g,
          '<span class="xml-attr">$1</span>$2<span class="xml-string">$3</span>',
        );
      return `<span class="xml-punct">${open}</span><span class="xml-tag">${name}</span>${highlightedAttrs}<span class="xml-punct">${close}</span>`;
    },
  );
}

export default function CodeView({ code, language = "xml", error = null }) {
  const lines = useMemo(() => (code ?? "").split("\n"), [code]);
  const html = useMemo(() => {
    if (!code) return "";
    const escaped = escapeHtml(code);
    return language === "xml" ? highlightXml(escaped) : escaped;
  }, [code, language]);

  if (!code) {
    return (
      <div className="code-view code-view--empty">
        Import a BPMN file to view its source here.
      </div>
    );
  }

  return (
    <div className="code-view">
      {error && (
        <div className="code-view-banner" role="status">
          <span className="code-view-banner-title">Not parseable as a diagram</span>
          <span className="code-view-banner-detail">{error}</span>
        </div>
      )}
      <div className="code-view-scroll">
        <pre className="code-view-pre">
          <code
            className="code-view-gutter"
            aria-hidden="true"
          >{lines.map((_, i) => `${i + 1}`).join("\n")}</code>
          <code
            className="code-view-code language-xml"
            dangerouslySetInnerHTML={{ __html: html }}
          />
        </pre>
      </div>
    </div>
  );
}
