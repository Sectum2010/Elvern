import { QueryClient } from "@tanstack/react-query";


export const queryClient = new QueryClient();


function isProtectedQuery(query) {
  return query?.queryKey?.[0] === "library";
}


export function clearProtectedQueryCache() {
  void queryClient.cancelQueries({ predicate: isProtectedQuery });
  queryClient.removeQueries({ predicate: isProtectedQuery });
}
