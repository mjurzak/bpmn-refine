import { useEffect, useRef, useState } from "react";
import NavigatedViewer from "bpmn-js/lib/NavigatedViewer";
import "bpmn-js/dist/assets/bpmn-js.css";
import "bpmn-js/dist/assets/diagram-js.css";
import "bpmn-js/dist/assets/bpmn-font/css/bpmn.css";

const MARKER_CLASSES = ["diff-removed", "diff-added", "diff-modified"];

export default function BpmnComparisonViewer({ xml, markers, label }) {
  const containerRef = useRef(null);
  const viewerRef = useRef(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    const viewer = new NavigatedViewer({ container: containerRef.current });
    viewerRef.current = viewer;
    return () => {
      viewerRef.current = null;
      viewer.destroy();
    };
  }, []);

  useEffect(() => {
    const viewer = viewerRef.current;
    if (!viewer || !xml) return undefined;
    let cancelled = false;

    setError(null);
    viewer
      .importXML(xml)
      .then(() => {
        if (cancelled) return;
        const canvas = viewer.get("canvas");
        const registry = viewer.get("elementRegistry");
        for (const { id } of registry.getAll()) {
          for (const marker of MARKER_CLASSES) canvas.removeMarker(id, marker);
        }
        for (const [marker, ids] of Object.entries(markers)) {
          for (const id of ids) {
            if (registry.get(id)) canvas.addMarker(id, marker);
          }
        }
        canvas.resized();
        canvas.zoom("fit-viewport", "auto");
      })
      .catch((reason) => {
        if (!cancelled) setError(reason.message ?? "Could not render BPMN");
      });

    return () => {
      cancelled = true;
    };
  }, [xml, markers]);

  return (
    <div className="dataset-viewer" aria-label={label}>
      <div ref={containerRef} className="dataset-viewer-canvas" />
      {error ? <div className="dataset-viewer-error">{error}</div> : null}
    </div>
  );
}
