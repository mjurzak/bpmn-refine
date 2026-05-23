import { useState, useCallback, useRef, useEffect } from "react";

// generic drag-to-resize hook
// axis: "horizontal" (changes width) or "vertical" (changes height)
// direction controls whether dragging toward larger coordinates increases size
// use -1 for right-anchored panels whose left handle is dragged
// returns { size, isDragging, handleMouseDown }
export default function useResize({ initial, min, max, axis = "horizontal", direction = 1 }) {
  const [size, setSize] = useState(initial);
  const [isDragging, setIsDragging] = useState(false);
  const startPos = useRef(0);
  const startSize = useRef(0);

  const handleMouseDown = useCallback(
    (e) => {
      e.preventDefault();
      startPos.current = axis === "horizontal" ? e.clientX : e.clientY;
      startSize.current = size;
      setIsDragging(true);
    },
    [size, axis]
  );

  useEffect(() => {
    if (!isDragging) return;

    function onMouseMove(e) {
      const pos = axis === "horizontal" ? e.clientX : e.clientY;
      const delta = pos - startPos.current;
      const next = Math.min(max, Math.max(min, startSize.current + delta * direction));
      setSize(next);
    }

    function onMouseUp() {
      setIsDragging(false);
    }

    // prevent text selection while dragging
    document.body.style.userSelect = "none";
    document.body.style.cursor = axis === "horizontal" ? "col-resize" : "row-resize";

    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("mouseup", onMouseUp);

    return () => {
      document.body.style.userSelect = "";
      document.body.style.cursor = "";
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("mouseup", onMouseUp);
    };
  }, [isDragging, axis, min, max, direction]);

  return { size, isDragging, handleMouseDown };
}
