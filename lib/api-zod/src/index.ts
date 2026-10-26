export * from "./generated/api";
export * from "./generated/types";

// `./generated/api` and `./generated/types` both declare the query-parameter
// types below (Orval emits an inline copy per operation and a shared copy under
// `types/`). The two barrels therefore collide; re-exporting the names from one
// module resolves the ambiguity deterministically.
export {
  AnalyticsExportsDownloadRetrieveParams,
  ClaimsEventsListParams,
  ClinicsAuditListParams,
  ConnectorsRunsListParams,
  ImagingStudiesInferenceListParams,
  PatientsAllergiesListParams,
  PatientsChartHistoryRetrieveParams,
  PatientsChartRetrieveParams,
  PatientsEncountersListParams,
  PatientsExportRetrieveParams,
  PatientsMedicalHistoryListParams,
  PatientsTimelineRetrieveParams,
  PublicClinicsAvailabilityRetrieveParams,
} from "./generated/api";
