import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useRef, useState } from "react";
import { Pencil, Sparkles } from "lucide-react";


const MENU_EDGE_MARGIN_PX = 8;
const DEFAULT_MENU_WIDTH_PX = 176;
const DEFAULT_MENU_HEIGHT_PX = 96;
const PosterContextMenuContext = createContext({ openPosterContextMenu: () => false });


function clampUnitInterval(value) {
  return Math.min(1, Math.max(0, value));
}


function resolveAnchorClientPosition(menu) {
  const anchorNode = menu?.anchorNode;
  if (
    anchorNode?.isConnected
    && typeof anchorNode.getBoundingClientRect === "function"
    && Number.isFinite(menu.anchorRatioX)
    && Number.isFinite(menu.anchorRatioY)
  ) {
    const rect = anchorNode.getBoundingClientRect();
    if (rect.width > 0 && rect.height > 0) {
      return {
        clientX: rect.left + (rect.width * menu.anchorRatioX),
        clientY: rect.top + (rect.height * menu.anchorRatioY),
      };
    }
  }
  return {
    clientX: menu.anchorPageX - (Number(window.scrollX) || 0),
    clientY: menu.anchorPageY - (Number(window.scrollY) || 0),
  };
}


export function clampPosterContextMenuPosition({
  clientX,
  clientY,
  menuWidth,
  menuHeight,
  viewportWidth,
  viewportHeight,
}) {
  const maxLeft = Math.max(MENU_EDGE_MARGIN_PX, viewportWidth - menuWidth - MENU_EDGE_MARGIN_PX);
  const maxTop = Math.max(MENU_EDGE_MARGIN_PX, viewportHeight - menuHeight - MENU_EDGE_MARGIN_PX);
  return {
    left: Math.min(Math.max(MENU_EDGE_MARGIN_PX, clientX), maxLeft),
    top: Math.min(Math.max(MENU_EDGE_MARGIN_PX, clientY), maxTop),
  };
}


export function PosterContextMenuProvider({ children, enabled = false }) {
  const [menu, setMenu] = useState(null);
  const menuRef = useRef(null);
  const positionFrameRef = useRef(0);

  const syncMenuPosition = useCallback(() => {
    setMenu((current) => {
      if (!current) {
        return current;
      }
      const anchorPosition = resolveAnchorClientPosition(current);
      const left = anchorPosition.clientX + current.offsetX;
      const top = anchorPosition.clientY + current.offsetY;
      if (left === current.left && top === current.top) {
        return current;
      }
      return { ...current, left, top };
    });
  }, []);

  const scheduleMenuPositionSync = useCallback(() => {
    if (positionFrameRef.current || typeof window === "undefined") {
      return;
    }
    positionFrameRef.current = window.requestAnimationFrame(() => {
      positionFrameRef.current = 0;
      syncMenuPosition();
    });
  }, [syncMenuPosition]);

  const openPosterContextMenu = useCallback((event, item) => {
    if (!enabled || typeof window === "undefined") {
      return false;
    }
    const clientX = Number(event?.clientX) || 0;
    const clientY = Number(event?.clientY) || 0;
    const anchorNode = event?.currentTarget || null;
    const anchorRect = anchorNode?.getBoundingClientRect?.();
    const hasMeasuredAnchor = Boolean(anchorRect?.width > 0 && anchorRect?.height > 0);
    const anchorRatioX = hasMeasuredAnchor
      ? clampUnitInterval((clientX - anchorRect.left) / anchorRect.width)
      : null;
    const anchorRatioY = hasMeasuredAnchor
      ? clampUnitInterval((clientY - anchorRect.top) / anchorRect.height)
      : null;
    const eventPageX = Number(event?.pageX);
    const eventPageY = Number(event?.pageY);
    const anchorPageX = Number.isFinite(eventPageX)
      ? eventPageX
      : clientX + (Number(window.scrollX) || 0);
    const anchorPageY = Number.isFinite(eventPageY)
      ? eventPageY
      : clientY + (Number(window.scrollY) || 0);
    const position = clampPosterContextMenuPosition({
      clientX,
      clientY,
      menuWidth: DEFAULT_MENU_WIDTH_PX,
      menuHeight: DEFAULT_MENU_HEIGHT_PX,
      viewportWidth: window.innerWidth,
      viewportHeight: window.innerHeight,
    });
    setMenu({
      itemId: item?.id ?? null,
      title: String(item?.title || "Untitled"),
      anchorNode,
      anchorRatioX,
      anchorRatioY,
      anchorPageX,
      anchorPageY,
      offsetX: position.left - clientX,
      offsetY: position.top - clientY,
      placementResolved: false,
      ...position,
    });
    return true;
  }, [enabled]);

  useEffect(() => {
    if (!enabled) {
      setMenu(null);
    }
  }, [enabled]);

  useLayoutEffect(() => {
    if (!menu || menu.placementResolved || !menuRef.current || typeof window === "undefined") {
      return;
    }
    const anchorPosition = resolveAnchorClientPosition(menu);
    const rect = menuRef.current.getBoundingClientRect();
    const nextPosition = clampPosterContextMenuPosition({
      clientX: anchorPosition.clientX,
      clientY: anchorPosition.clientY,
      menuWidth: rect.width || DEFAULT_MENU_WIDTH_PX,
      menuHeight: rect.height || DEFAULT_MENU_HEIGHT_PX,
      viewportWidth: window.innerWidth,
      viewportHeight: window.innerHeight,
    });
    setMenu((current) => current ? {
      ...current,
      ...nextPosition,
      offsetX: nextPosition.left - anchorPosition.clientX,
      offsetY: nextPosition.top - anchorPosition.clientY,
      placementResolved: true,
    } : current);
  }, [menu]);

  const menuOpen = Boolean(menu);

  useEffect(() => {
    if (!menuOpen) {
      return undefined;
    }
    function handleOutsidePointerDown(event) {
      if (!menuRef.current?.contains(event.target)) {
        setMenu(null);
      }
    }
    document.addEventListener("pointerdown", handleOutsidePointerDown, true);
    return () => {
      document.removeEventListener("pointerdown", handleOutsidePointerDown, true);
    };
  }, [menuOpen]);

  useEffect(() => {
    if (!menuOpen || typeof window === "undefined") {
      return undefined;
    }
    const visualViewport = window.visualViewport || null;
    const observer = typeof ResizeObserver === "function" ? new ResizeObserver(scheduleMenuPositionSync) : null;
    if (menu?.anchorNode?.isConnected) {
      observer?.observe(menu.anchorNode);
    }
    document.addEventListener("scroll", scheduleMenuPositionSync, { capture: true, passive: true });
    window.addEventListener("scroll", scheduleMenuPositionSync, { passive: true });
    window.addEventListener("resize", scheduleMenuPositionSync);
    visualViewport?.addEventListener("scroll", scheduleMenuPositionSync, { passive: true });
    visualViewport?.addEventListener("resize", scheduleMenuPositionSync);
    return () => {
      observer?.disconnect();
      document.removeEventListener("scroll", scheduleMenuPositionSync, true);
      window.removeEventListener("scroll", scheduleMenuPositionSync);
      window.removeEventListener("resize", scheduleMenuPositionSync);
      visualViewport?.removeEventListener("scroll", scheduleMenuPositionSync);
      visualViewport?.removeEventListener("resize", scheduleMenuPositionSync);
      if (positionFrameRef.current) {
        window.cancelAnimationFrame(positionFrameRef.current);
        positionFrameRef.current = 0;
      }
    };
  }, [menu?.anchorNode, menuOpen, scheduleMenuPositionSync]);

  return (
    <PosterContextMenuContext.Provider value={{ openPosterContextMenu }}>
      {children}
      {menu ? (
        <div
          aria-label={`Poster actions for ${menu.title}`}
          className="poster-context-menu"
          data-library-item-id={menu.itemId ?? undefined}
          ref={menuRef}
          role="menu"
          style={{ left: menu.left, top: menu.top }}
        >
          <button className="poster-context-menu__action" role="menuitem" type="button">
            <Pencil aria-hidden="true" size={17} />
            <span>Edit</span>
          </button>
          <button className="poster-context-menu__action" role="menuitem" type="button">
            <Sparkles aria-hidden="true" size={17} />
            <span>Generate</span>
          </button>
        </div>
      ) : null}
    </PosterContextMenuContext.Provider>
  );
}


export function usePosterContextMenu() {
  return useContext(PosterContextMenuContext);
}
