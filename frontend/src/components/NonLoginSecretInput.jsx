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


export function NonLoginSecretInput({
  autoComplete = "new-password",
  purpose: _purpose = "secret",
  ...inputProps
}) {
  const tokenRef = useRef(createFieldToken());
  const fieldName = `elvern-secret-${tokenRef.current}`;

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
