import { useRef } from "react";
import { PasswordInput } from "./PasswordInput";


function createFieldToken() {
  if (typeof crypto !== "undefined" && typeof crypto.getRandomValues === "function") {
    const values = new Uint32Array(2);
    crypto.getRandomValues(values);
    return `${values[0].toString(36)}${values[1].toString(36)}`;
  }
  return Math.random().toString(36).slice(2, 14);
}


function normalizePurpose(value) {
  return String(value || "secret")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "") || "secret";
}


export function NonLoginSecretInput({
  autoComplete = "new-password",
  purpose = "secret",
  ...inputProps
}) {
  const tokenRef = useRef(createFieldToken());
  const safePurpose = normalizePurpose(purpose);
  const fieldName = `elvern-${safePurpose}-${tokenRef.current}`;

  return (
    <PasswordInput
      {...inputProps}
      autoCapitalize="off"
      autoComplete={autoComplete}
      autoCorrect="off"
      data-1p-ignore="true"
      data-bwignore="true"
      data-form-type="other"
      data-lpignore="true"
      id={fieldName}
      name={fieldName}
      spellCheck="false"
    />
  );
}
