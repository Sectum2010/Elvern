import { QueryClient } from "@tanstack/react-query";


export const queryClient = new QueryClient();


function isProtectedQuery(query) {
  return query?.queryKey?.[0] === "library"
    || query?.queryKey?.[0] === "user-settings"
    || query?.queryKey?.[0] === "library-revision"
    || query?.queryKey?.[0] === "control-center";
}


export function clearProtectedQueryCache() {
  void queryClient.cancelQueries({ predicate: isProtectedQuery });
  queryClient.removeQueries({ predicate: isProtectedQuery });
}
