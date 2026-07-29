import { createContext, useContext, useMemo, useState } from "react";


const DesktopLibraryIslandContext = createContext({
  publishLibraryState: () => {},
  libraryState: null,
});


export function DesktopLibraryIslandProvider({ children }) {
  const [libraryState, setLibraryState] = useState(null);
  const value = useMemo(() => ({
    libraryState,
    publishLibraryState: setLibraryState,
  }), [libraryState]);

  return (
    <DesktopLibraryIslandContext.Provider value={value}>
      {children}
    </DesktopLibraryIslandContext.Provider>
  );
}


export function useDesktopLibraryIslandContext() {
  return useContext(DesktopLibraryIslandContext);
}
