import { useEffect, useMemo, useState } from "react";

import {
  getDatasetComparison,
  getDatasetIndex,
} from "../api/client.js";
import { compareBpmnXml } from "../utils/bpmnXmlDiff.js";
import BpmnComparisonViewer from "./BpmnComparisonViewer.jsx";

const DATASET_VERSION = "v1.3.0";

export default function DatasetComparePage() {
  const [index, setIndex] = useState(null);
  const [selectedSeed, setSelectedSeed] = useState("");
  const [selectedVariant, setSelectedVariant] = useState("");
  const [comparison, setComparison] = useState(null);
  const [indexLoading, setIndexLoading] = useState(true);
  const [comparisonLoading, setComparisonLoading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    const controller = new AbortController();
    getDatasetIndex(DATASET_VERSION, controller.signal)
      .then((payload) => {
        const firstSeed = payload.seeds[0];
        setIndex(payload);
        setSelectedSeed(firstSeed?.id ?? "");
        setSelectedVariant(firstSeed?.variants[0]?.id ?? "");
        setError(null);
      })
      .catch((reason) => {
        if (reason.name !== "AbortError") setError(reason.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setIndexLoading(false);
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!selectedVariant) {
      setComparison(null);
      return undefined;
    }
    const controller = new AbortController();
    setComparisonLoading(true);
    getDatasetComparison(
      DATASET_VERSION,
      selectedVariant,
      controller.signal,
    )
      .then((payload) => {
        setComparison(payload);
        setError(null);
      })
      .catch((reason) => {
        if (reason.name !== "AbortError") setError(reason.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setComparisonLoading(false);
      });
    return () => controller.abort();
  }, [selectedVariant]);

  const seed = index?.seeds.find((item) => item.id === selectedSeed);
  const diff = useMemo(() => {
    if (!comparison) return { removed: [], added: [], modified: [] };
    return compareBpmnXml(
      comparison.original_xml,
      comparison.variant_xml,
    );
  }, [comparison]);
  const originalMarkers = useMemo(
    () => ({
      "diff-removed": diff.removed,
      "diff-modified": diff.modified,
    }),
    [diff],
  );
  const variantMarkers = useMemo(
    () => ({
      "diff-added": diff.added,
      "diff-modified": diff.modified,
    }),
    [diff],
  );

  function selectSeed(seedId) {
    const nextSeed = index?.seeds.find((item) => item.id === seedId);
    setSelectedSeed(seedId);
    setSelectedVariant(nextSeed?.variants[0]?.id ?? "");
  }

  return (
    <div className="dataset-compare-page">
      <header className="dataset-compare-header">
        <div>
          <div className="dataset-compare-title">Dataset visual comparison</div>
          <div className="dataset-compare-subtitle">
            {DATASET_VERSION} · original seed against a matched variant
          </div>
        </div>
        <a className="toolbar-btn" href="/">
          Back to editor
        </a>
      </header>

      <section className="dataset-compare-controls" aria-label="Comparison selection">
        <label>
          <span>Original seed</span>
          <select
            aria-label="Original seed"
            value={selectedSeed}
            onChange={(event) => selectSeed(event.target.value)}
            disabled={!index?.seeds.length}
          >
            {(index?.seeds ?? []).map((item) => (
              <option key={item.id} value={item.id}>
                Seed {item.id}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>Matched variant</span>
          <select
            aria-label="Matched variant"
            value={selectedVariant}
            onChange={(event) => setSelectedVariant(event.target.value)}
            disabled={!seed?.variants.length}
          >
            {(seed?.variants ?? []).map((variant) => (
              <option key={variant.id} value={variant.id}>
                {variant.operators.join(" + ")} · {variant.id}
              </option>
            ))}
          </select>
        </label>
        {comparison ? (
          <div className="dataset-compare-ground-truth">
            <span>{comparison.variant.defect_class}</span>
            Expected: {comparison.variant.expected_finding}
          </div>
        ) : null}
      </section>

      {error ? <div className="dataset-compare-alert">{error}</div> : null}

      <section className="dataset-diff-summary" aria-label="Visual diff legend">
        <DiffGroup label="Removed" tone="removed" ids={diff.removed} />
        <DiffGroup label="Added" tone="added" ids={diff.added} />
        <DiffGroup label="Modified" tone="modified" ids={diff.modified} />
      </section>

      <main
        className="dataset-compare-grid"
        aria-busy={indexLoading || comparisonLoading}
      >
        <DiagramPane
          title={`Original · Seed ${(comparison?.seed ?? selectedSeed) || "—"}`}
          subtitle="Elements missing from the variant are marked red"
          xml={comparison?.original_xml}
          markers={originalMarkers}
        />
        <DiagramPane
          title={`Variant · ${(comparison?.variant.id ?? selectedVariant) || "—"}`}
          subtitle="Added elements are green; modified elements are amber"
          xml={comparison?.variant_xml}
          markers={variantMarkers}
        />
        {indexLoading || comparisonLoading ? (
          <div className="dataset-compare-loading">Loading comparison…</div>
        ) : null}
      </main>
    </div>
  );
}

function DiagramPane({ title, subtitle, xml, markers }) {
  return (
    <article className="dataset-diagram-pane">
      <header>
        <h2>{title}</h2>
        <p>{subtitle}</p>
      </header>
      {xml ? (
        <BpmnComparisonViewer xml={xml} markers={markers} label={title} />
      ) : (
        <div className="dataset-viewer-placeholder">Select a comparison</div>
      )}
    </article>
  );
}

function DiffGroup({ label, tone, ids }) {
  return (
    <div className={`dataset-diff-group dataset-diff-group--${tone}`}>
      <div className="dataset-diff-label">
        <span className="dataset-diff-dot" />
        {label} <strong>{ids.length}</strong>
      </div>
      <div className="dataset-diff-ids">
        {ids.length ? ids.join(", ") : "None"}
      </div>
    </div>
  );
}
