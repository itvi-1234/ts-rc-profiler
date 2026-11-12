import type { ValidateFunction } from "ajv/dist/2020.js";
import { stringify } from "yaml";

import type { Binding, Catalog } from "./bindings.js";
import { fail } from "./errors.js";
import type { Extraction } from "./extract.js";
import { compile, describeErrors, loadSchema, type JsonObject } from "./schema.js";

export interface ProfileOptions {
  name: string;
  workloadUri: string;
  workloadVersion?: string;
}

interface ExtensionSchema {
  coordinate: string;
  kind?: string;
  interfaceType?: string;
  exact: JsonObject;
}

export function buildProfile(extraction: Extraction, catalog: Catalog, options: ProfileOptions): JsonObject {
  if (extraction.conditions.length === 0) {
    fail("extract", "no declaration from an installed binding package was found in the project");
  }
  const workload: JsonObject = { uri: options.workloadUri };
  if (options.workloadVersion) {
    workload.version = options.workloadVersion;
  }
  const profile: JsonObject = {
    apiVersion: "runtimeconditions.io/v1alpha1",
    kind: "RuntimeConditionsProfile",
    metadata: { name: options.name },
    workload,
    extensions: extensionReferences(extraction.used),
    conditions: extraction.conditions,
  };
  validateProfile(profile, catalog.closure(extraction.used));
  return profile;
}

/**
 * Lists each used extension once, as `<short-url>:<version>`. The short URL is the
 * extension id without its trailing `/<version>/runtimeconditions.extension.yaml`.
 * An id that is not an https URL ending that way, or a binding without a recorded
 * version, is written as the bare id, which the spec allows.
 */
export function extensionReference(id: string, version: string | undefined): string {
  if (!version) {
    return id;
  }
  const tail = `/${version}/runtimeconditions.extension.yaml`;
  return id.startsWith("https://") && id.endsWith(tail) ? `${id.slice(0, -tail.length)}:${version}` : `${id}:${version}`;
}

function extensionReferences(used: Binding[]): string[] {
  const references = new Set(used.map((binding) => extensionReference(binding.extensionId, binding.extensionVersion)));
  return [...references].sort((a, b) => (a < b ? -1 : a > b ? 1 : 0));
}

export function validateProfile(profile: JsonObject, closure: Binding[]): void {
  const core = compile(loadSchema("runtimeconditions.profile.schema.yaml"));
  const coreValid: boolean = core(profile);
  if (!coreValid) {
    fail("schema", `the profile does not match the core profile schema: ${describeErrors(core)}`);
  }
  const schemas = new Map<string, ExtensionSchema>();
  for (const binding of closure) {
    for (const item of (binding.model.schemas ?? []) as unknown as ExtensionSchema[]) {
      schemas.set(item.coordinate, item);
    }
  }
  const compiled = new Map<string, ValidateFunction>();
  (profile.conditions as JsonObject[]).forEach((condition, index) => {
    const type = (condition.interface as JsonObject).type;
    for (const item of schemas.values()) {
      if (item.kind !== condition.kind || (item.interfaceType !== undefined && item.interfaceType !== type)) {
        continue;
      }
      let validate = compiled.get(item.coordinate);
      if (!validate) {
        validate = compile(item.exact);
        compiled.set(item.coordinate, validate);
      }
      const valid: boolean = validate(condition);
      if (!valid) {
        fail("schema", `condition ${index + 1} (${String(condition.kind)}) does not satisfy ${item.coordinate}: ${describeErrors(validate)}`);
      }
    }
  });
}

export function profileYaml(profile: JsonObject): string {
  return stringify(profile, { lineWidth: 0 });
}
