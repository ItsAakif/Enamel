import { defineConfig, InputTransformerFn } from "orval";
import path from "path";

const root = path.resolve(__dirname, "..", "..");
const apiClientReactSrc = path.resolve(root, "lib", "api-client-react", "src");
const apiZodSrc = path.resolve(root, "lib", "api-zod", "src");

// ``codegen:check`` points these at a scratch directory so drift detection
// never mutates the committed output.
const clientWorkspace = process.env.ORVAL_CLIENT_WORKSPACE
  ? path.resolve(process.env.ORVAL_CLIENT_WORKSPACE)
  : apiClientReactSrc;
const zodWorkspace = process.env.ORVAL_ZOD_WORKSPACE
  ? path.resolve(process.env.ORVAL_ZOD_WORKSPACE)
  : apiZodSrc;

// The committed Django export is the single source of truth (ADR-007/012).
// Paths in the document already carry the `/api/v1/` prefix, so no baseUrl is
// applied here.
const djangoSchema = path.resolve(root, "backend", "openapi", "schema.json");

// Our exports make assumptions about the title of the API being "Api" (i.e. generated output is `api.ts`).
const titleTransformer: InputTransformerFn = (config) => {
  config.info ??= {};
  config.info.title = "Api";

  return config;
};

export default defineConfig({
  "api-client-react": {
    input: {
      target: djangoSchema,
      override: {
        transformer: titleTransformer,
      },
    },
    output: {
      workspace: clientWorkspace,
      target: "generated",
      client: "react-query",
      mode: "split",
      clean: true,
      prettier: true,
      override: {
        fetch: {
          includeHttpResponseReturnType: false,
        },
        mutator: {
          path: path.resolve(apiClientReactSrc, "custom-fetch.ts"),
          name: "customFetch",
        },
      },
    },
  },
  zod: {
    input: {
      target: djangoSchema,
      override: {
        transformer: titleTransformer,
      },
    },
    output: {
      workspace: zodWorkspace,
      client: "zod",
      target: "generated",
      schemas: { path: "generated/types", type: "typescript" },
      mode: "split",
      clean: true,
      prettier: true,
      override: {
        zod: {
          coerce: {
            query: ["boolean", "number", "string"],
            param: ["boolean", "number", "string"],
            body: ["bigint", "date"],
            response: ["bigint", "date"],
          },
        },
        useDates: true,
        useBigInt: true,
      },
    },
  },
});
