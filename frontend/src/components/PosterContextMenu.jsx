import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useRef, useState } from "react";
import { Pencil, Sparkles } from "lucide-react";


const MENU_EDGE_MARGIN_PX = 8;
const DEFAULT_MENU_WIDTH_PX = 176;
const DEFAULT_MENU_HEIGHT_PX = 96;
const PosterContextMenuContext = createContext({ openPosterContextMenu: () => false });


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

  const openPosterContextMenu = useCallback((event, item) => {
    if (!enabled || typeof window === "undefined") {
      return false;
    }
    const clientX = Number(event?.clientX) || 0;
    const clientY = Number(event?.clientY) || 0;
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
      clientX,
      clientY,
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
    if (!menu || !menuRef.current || typeof window === "undefined") {
      return;
    }
    const rect = menuRef.current.getBoundingClientRect();
    const nextPosition = clampPosterContextMenuPosition({
      clientX: menu.clientX,
      clientY: menu.clientY,
      menuWidth: rect.width || DEFAULT_MENU_WIDTH_PX,
      menuHeight: rect.height || DEFAULT_MENU_HEIGHT_PX,
      viewportWidth: window.innerWidth,
      viewportHeight: window.innerHeight,
    });
    if (nextPosition.left !== menu.left || nextPosition.top !== menu.top) {
      setMenu((current) => current ? { ...current, ...nextPosition } : current);
    }
  }, [menu]);

  useEffect(() => {
    if (!menu) {
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
  }, [menu]);

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
            <Pencil aria-hidden="true" className="poster-context-menu__pencil" size={17} />
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
