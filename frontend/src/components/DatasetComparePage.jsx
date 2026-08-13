import { useEffect, useMemo, useState } from "react";

import {
  getDatasetComparison,
  getDatasetIndex,
  getEnhancementComparison,
  getEnhancementIndex,
} from "../api/client.js";
import { compareBpmnXml } from "../utils/bpmnXmlDiff.js";
import BpmnComparisonViewer from "./BpmnComparisonViewer.jsx";

const DATASET_VERSION = "v1.0";
const DEFECT_MODE = "defects";
const ENHANCEMENT_MODE = "enhancement";

export default function DatasetComparePage() {
  const [mode, setMode] = useState(DEFECT_MODE);
  const [index, setIndex] = useState(null);
  const [selectedSeed, setSelectedSeed] = useState("");
  const [selectedVariant, setSelectedVariant] = useState("");
  const [selectedEnhancement, setSelectedEnhancement] = useState("");
  const [comparison, setComparison] = useState(null);
  const [indexLoading, setIndexLoading] = useState(true);
  const [comparisonLoading, setComparisonLoading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    const controller = new AbortController();
    setIndexLoading(true);
    setComparison(null);
    const loadIndex =
      mode === ENHANCEMENT_MODE
        ? getEnhancementIndex(controller.signal)
        : getDatasetIndex(DATASET_VERSION, controller.signal);
    loadIndex
      .then((payload) => {
        setIndex(payload);
        if (mode === ENHANCEMENT_MODE) {
          setSelectedEnhancement(payload.cases[0]?.id ?? "");
        } else {
          const firstSeed = payload.seeds[0];
          setSelectedSeed(firstSeed?.id ?? "");
          setSelectedVariant(firstSeed?.variants[0]?.id ?? "");
        }
        setError(null);
      })
      .catch((reason) => {
        if (reason.name !== "AbortError") setError(reason.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setIndexLoading(false);
      });
    return () => controller.abort();
  }, [mode]);

  useEffect(() => {
    const selectedId =
      mode === ENHANCEMENT_MODE ? selectedEnhancement : selectedVariant;
    if (!selectedId) {
      setComparison(null);
      return undefined;
    }
    const controller = new AbortController();
    setComparisonLoading(true);
    const loadComparison =
      mode === ENHANCEMENT_MODE
        ? getEnhancementComparison(selectedId, controller.signal)
        : getDatasetComparison(DATASET_VERSION, selectedId, controller.signal);
    loadComparison
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
  }, [mode, selectedEnhancement, selectedVariant]);

  const seed = index?.seeds?.find((item) => item.id === selectedSeed);
  const defectComparison =
    mode === DEFECT_MODE && comparison?.variant ? comparison : null;
  const enhancementComparison =
    mode === ENHANCEMENT_MODE && comparison?.case ? comparison : null;
  const enhancementCase = enhancementComparison?.case;
  const leftXml =
    enhancementComparison?.core_xml ?? defectComparison?.original_xml;
  const rightXml =
    enhancementComparison?.reference_xml ?? defectComparison?.variant_xml;
  const diff = useMemo(() => {
    if (!leftXml || !rightXml) return { removed: [], added: [], modified: [] };
    return compareBpmnXml(leftXml, rightXml);
  }, [leftXml, rightXml]);
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
            {mode === ENHANCEMENT_MODE
              ? "M_core against the expected enhanced model"
              : `${DATASET_VERSION} · original seed against a matched variant`}
          </div>
        </div>
        <a className="toolbar-btn" href="/">
          Back to editor
        </a>
      </header>

      <section className="dataset-compare-controls" aria-label="Comparison selection">
        <label>
          <span>Dataset view</span>
          <select
            aria-label="Dataset view"
            value={mode}
            onChange={(event) => setMode(event.target.value)}
          >
            <option value={DEFECT_MODE}>Defect variants</option>
            <option value={ENHANCEMENT_MODE}>Enhancement</option>
          </select>
        </label>
        {mode === ENHANCEMENT_MODE ? (
          <label className="dataset-compare-case-select">
            <span>Enhancement case</span>
            <select
              aria-label="Enhancement case"
              value={selectedEnhancement}
              onChange={(event) => setSelectedEnhancement(event.target.value)}
              disabled={!index?.cases?.length}
            >
              {(index?.cases ?? []).map((item) => (
                <option key={item.id} value={item.id}>
                  {item.operator} · Seed {item.seed_id} · {item.id}
                </option>
              ))}
            </select>
          </label>
        ) : (
          <>
            <label>
              <span>Original seed</span>
              <select
                aria-label="Original seed"
                value={selectedSeed}
                onChange={(event) => selectSeed(event.target.value)}
                disabled={!index?.seeds?.length}
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
          </>
        )}
        {defectComparison ? (
          <div className="dataset-compare-ground-truth">
            <span>{defectComparison.variant.defect_class}</span>
            Expected: {defectComparison.variant.expected_finding}
          </div>
        ) : null}
        {enhancementCase ? (
          <div className="dataset-compare-ground-truth">
            <span>{enhancementCase.operator}</span>
            {enhancementCase.relation_type} · {enhancementCase.expected_element_ids.join(", ")}
          </div>
        ) : null}
      </section>

      {error ? <div className="dataset-compare-alert">{error}</div> : null}

      {enhancementCase ? (
        <section className="dataset-enhancement-contract" aria-label="Enhancement contract">
          <div>
            <span>Instruction</span>
            <p>{enhancementCase.instruction}</p>
          </div>
          <div>
            <span>Semantic contract</span>
            <p>
              {enhancementCase.relation_type} · expected IDs: {enhancementCase.expected_element_ids.join(", ")}
            </p>
          </div>
        </section>
      ) : null}

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
          title={
            mode === ENHANCEMENT_MODE
              ? `M_core · Seed ${enhancementCase?.seed_id ?? "—"}`
              : `Original · Seed ${(defectComparison?.seed ?? selectedSeed) || "—"}`
          }
          subtitle={
            mode === ENHANCEMENT_MODE
              ? "Valid model before the requested enhancement"
              : "Elements missing from the variant are marked red"
          }
          xml={leftXml}
          markers={originalMarkers}
        />
        <DiagramPane
          title={
            mode === ENHANCEMENT_MODE
              ? `Reference · ${enhancementCase?.id ?? selectedEnhancement ?? "—"}`
              : `Variant · ${(defectComparison?.variant.id ?? selectedVariant) || "—"}`
          }
          subtitle={
            mode === ENHANCEMENT_MODE
              ? "Expected enhancement; additions are green"
              : "Added elements are green; modified elements are amber"
          }
          xml={rightXml}
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
