import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { MapPin, Pencil, Plus, Trash2 } from "lucide-react";
import {
  clinicsLocationsCreate,
  clinicsLocationsDestroy,
  clinicsLocationsList,
  clinicsLocationsPartialUpdate,
  getClinicsLocationsListQueryKey,
  type Location,
} from "@workspace/api-client-react";
import { useAuth } from "@/hooks/use-auth";
import { apiErrorFieldErrors, apiErrorMessage, toPage } from "@/lib/api";
import { DataState } from "@/components/common/DataState";
import {
  DangerButton,
  ErrorBanner,
  Field,
  Pill,
  PrimaryButton,
  SectionCard,
  SecondaryButton,
  TextInput,
} from "@/components/settings/settings-ui";

interface LocationForm {
  name: string;
  phone: string;
  timezone: string;
  is_active: boolean;
}

const EMPTY_FORM: LocationForm = { name: "", phone: "", timezone: "", is_active: true };

export function LocationsSection() {
  const { activeClinicId } = useAuth();
  const queryClient = useQueryClient();
  const clinicId = activeClinicId ?? "";

  const locationsQuery = useQuery({
    queryKey: getClinicsLocationsListQueryKey(clinicId),
    queryFn: async () => toPage<Location>(await clinicsLocationsList(clinicId)).results,
    enabled: activeClinicId !== null,
    retry: false,
  });

  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<LocationForm>(EMPTY_FORM);
  const [formError, setFormError] = useState<string | null>(null);

  function invalidate() {
    void queryClient.invalidateQueries({ queryKey: getClinicsLocationsListQueryKey(clinicId) });
  }

  const createMutation = useMutation({
    mutationFn: (data: LocationForm) => clinicsLocationsCreate(clinicId, data),
    onSuccess: () => {
      setForm(EMPTY_FORM);
      setFormError(null);
      invalidate();
    },
    onError: (error) => setFormError(apiErrorMessage(error)),
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, data }: { id: string; data: LocationForm }) =>
      clinicsLocationsPartialUpdate(clinicId, id, data),
    onSuccess: () => {
      setEditingId(null);
      setForm(EMPTY_FORM);
      setFormError(null);
      invalidate();
    },
    onError: (error) => setFormError(apiErrorMessage(error)),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => clinicsLocationsDestroy(clinicId, id),
    onSuccess: () => invalidate(),
    onError: (error) => setFormError(apiErrorMessage(error)),
  });

  const fieldErrors = apiErrorFieldErrors(updateMutation.error ?? createMutation.error);
  const busy = createMutation.isPending || updateMutation.isPending;

  function startEdit(location: Location) {
    setEditingId(location.id);
    setForm({
      name: location.name,
      phone: location.phone ?? "",
      timezone: location.timezone ?? "",
      is_active: location.is_active ?? true,
    });
    setFormError(null);
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError(null);
    if (editingId) updateMutation.mutate({ id: editingId, data: form });
    else createMutation.mutate(form);
  }

  const locations = locationsQuery.data ?? [];

  return (
    <SectionCard icon={MapPin} title="Locations" description="Physical sites where care is delivered.">
      <DataState
        loading={locationsQuery.isPending}
        error={locationsQuery.error}
        empty={locations.length === 0}
        emptyTitle="No locations yet"
        emptyMessage="Add the clinic's first location below."
        onRetry={() => void locationsQuery.refetch()}
      >
        <ul className="space-y-3" data-testid="locations-list">
          {locations.map((location) => (
            <li
              key={location.id}
              className="flex flex-wrap items-center justify-between gap-3 p-4 bg-muted/50 rounded-xl"
            >
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-bold text-foreground">{location.name}</span>
                  <Pill tone={location.is_active ? "success" : "neutral"}>
                    {location.is_active ? "active" : "inactive"}
                  </Pill>
                </div>
                <p className="text-xs text-muted-foreground mt-0.5">
                  {location.phone || "no phone"} · {location.timezone || "no timezone"}
                </p>
              </div>
              <div className="flex items-center gap-2">
                <SecondaryButton
                  onClick={() => startEdit(location)}
                  aria-label={`Edit ${location.name}`}
                  className="px-4 py-2"
                >
                  <Pencil size={12} /> Edit
                </SecondaryButton>
                <DangerButton
                  busy={deleteMutation.isPending && deleteMutation.variables === location.id}
                  onClick={() => deleteMutation.mutate(location.id)}
                  aria-label={`Delete ${location.name}`}
                  className="px-4 py-2"
                >
                  <Trash2 size={12} /> Delete
                </DangerButton>
              </div>
            </li>
          ))}
        </ul>
      </DataState>

      <form onSubmit={onSubmit} className="mt-6 border-t border-border/50 pt-6 space-y-4" noValidate>
        <p className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground">
          {editingId ? "Edit location" : "Add location"}
        </p>
        {formError ? <ErrorBanner>{formError}</ErrorBanner> : null}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <Field label="Name" htmlFor="location-name" error={fieldErrors.name}>
            <TextInput
              id="location-name"
              data-testid="location-name"
              required
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
            />
          </Field>
          <Field label="Phone" htmlFor="location-phone" error={fieldErrors.phone}>
            <TextInput
              id="location-phone"
              value={form.phone}
              onChange={(e) => setForm({ ...form, phone: e.target.value })}
            />
          </Field>
          <Field label="Timezone" htmlFor="location-timezone" error={fieldErrors.timezone}>
            <TextInput
              id="location-timezone"
              value={form.timezone}
              onChange={(e) => setForm({ ...form, timezone: e.target.value })}
            />
          </Field>
          <label className="flex items-center gap-3 mt-6 text-sm font-medium text-foreground">
            <input
              type="checkbox"
              checked={form.is_active}
              onChange={(e) => setForm({ ...form, is_active: e.target.checked })}
              className="w-5 h-5 rounded border-border text-primary focus:ring-primary"
            />
            Active
          </label>
        </div>
        <div className="flex items-center gap-3">
          <PrimaryButton type="submit" data-testid="location-save" busy={busy}>
            <Plus size={14} /> {editingId ? "Save location" : "Add location"}
          </PrimaryButton>
          {editingId ? (
            <SecondaryButton
              onClick={() => {
                setEditingId(null);
                setForm(EMPTY_FORM);
                setFormError(null);
              }}
            >
              Cancel
            </SecondaryButton>
          ) : null}
        </div>
      </form>
    </SectionCard>
  );
}
