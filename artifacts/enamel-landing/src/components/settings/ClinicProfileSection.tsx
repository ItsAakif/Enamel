import { useEffect, useMemo, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Building2 } from "lucide-react";
import {
  clinicsPartialUpdate,
  clinicsRetrieve,
  getAuthMeRetrieveQueryKey,
  getClinicsRetrieveQueryKey,
  type Clinic,
} from "@workspace/api-client-react";
import { useAuth } from "@/hooks/use-auth";
import { apiErrorFieldErrors, apiErrorMessage } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { DataState } from "@/components/common/DataState";
import {
  ErrorBanner,
  Field,
  Pill,
  PrimaryButton,
  SectionCard,
  TextInput,
} from "@/components/settings/settings-ui";

interface FormState {
  name: string;
  phone: string;
  timezone: string;
  currency: string;
  locale: string;
  country: string;
}

function toForm(clinic: Clinic): FormState {
  return {
    name: clinic.name,
    phone: clinic.phone ?? "",
    timezone: clinic.timezone ?? "",
    currency: clinic.currency ?? "",
    locale: clinic.locale ?? "",
    country: clinic.country ?? "",
  };
}

export function ClinicProfileSection() {
  const { activeClinicId, refresh } = useAuth();
  const queryClient = useQueryClient();

  const clinicQuery = useQuery({
    queryKey: getClinicsRetrieveQueryKey(activeClinicId ?? ""),
    queryFn: () => clinicsRetrieve(activeClinicId as string),
    enabled: activeClinicId !== null,
    retry: false,
  });

  const clinic = clinicQuery.data ?? null;
  const [form, setForm] = useState<FormState | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  // The confirmation belongs to the clinic identity, not to the payload: the
  // refetch that follows a save must not wipe the "saved" banner, otherwise it
  // disappears again as soon as the server answers.
  const clinicId = clinic?.id ?? null;

  useEffect(() => {
    if (clinic) {
      setForm(toForm(clinic));
      setFormError(null);
    }
  }, [clinic]);

  useEffect(() => {
    setSaved(false);
  }, [clinicId]);

  // Editing any field invalidates the previous confirmation.
  function updateForm(patch: Partial<FormState>): void {
    setSaved(false);
    setForm((current) => (current ? { ...current, ...patch } : current));
  }

  const mutation = useMutation({
    mutationFn: (data: FormState) => clinicsPartialUpdate(activeClinicId as string, data),
    onSuccess: () => {
      setSaved(true);
      setFormError(null);
      void queryClient.invalidateQueries({ queryKey: getClinicsRetrieveQueryKey(activeClinicId ?? "") });
      void queryClient.invalidateQueries({ queryKey: getAuthMeRetrieveQueryKey() });
      void refresh();
    },
  });

  const fieldErrors = useMemo(() => apiErrorFieldErrors(mutation.error), [mutation.error]);

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!form) return;
    setSaved(false);
    setFormError(null);
    mutation.mutate(form, {
      onError: (error) => setFormError(apiErrorMessage(error)),
    });
  }

  return (
    <SectionCard icon={Building2} title="Clinic Profile" description="Practice name, contact details and locale.">
      <DataState
        loading={clinicQuery.isPending}
        error={clinicQuery.error}
        onRetry={() => void clinicQuery.refetch()}
      >
        {clinic && form ? (
          <form onSubmit={onSubmit} className="space-y-4" noValidate>
            {formError ? <ErrorBanner>{formError}</ErrorBanner> : null}
            {saved ? (
              <div role="status" className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700 font-medium">
                Clinic profile saved.
              </div>
            ) : null}

            <div className="flex items-center gap-3">
              <Pill tone={clinic.status === "active" ? "success" : "warning"}>{clinic.status}</Pill>
              <span className="text-xs text-muted-foreground">
                slug <code className="font-mono">{clinic.slug}</code> · updated {formatDateTime(clinic.updated_at)}
              </span>
            </div>

            <Field label="Practice name" htmlFor="clinic-name" error={fieldErrors.name}>
              <TextInput
                id="clinic-name"
                data-testid="clinic-name"
                required
                value={form.name}
                onChange={(e) => updateForm({ name: e.target.value })}
              />
            </Field>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <Field label="Primary phone" htmlFor="clinic-phone" error={fieldErrors.phone}>
                <TextInput
                  id="clinic-phone"
                  value={form.phone}
                  onChange={(e) => updateForm({ phone: e.target.value })}
                />
              </Field>
              <Field label="Timezone" htmlFor="clinic-timezone" error={fieldErrors.timezone}>
                <TextInput
                  id="clinic-timezone"
                  value={form.timezone}
                  onChange={(e) => updateForm({ timezone: e.target.value })}
                />
              </Field>
              <Field label="Currency" htmlFor="clinic-currency" error={fieldErrors.currency} hint="ISO-4217, e.g. INR">
                <TextInput
                  id="clinic-currency"
                  value={form.currency}
                  onChange={(e) => updateForm({ currency: e.target.value })}
                />
              </Field>
              <Field label="Locale" htmlFor="clinic-locale" error={fieldErrors.locale}>
                <TextInput
                  id="clinic-locale"
                  value={form.locale}
                  onChange={(e) => updateForm({ locale: e.target.value })}
                />
              </Field>
              <Field label="Country" htmlFor="clinic-country" error={fieldErrors.country} hint="ISO-3166 alpha-2">
                <TextInput
                  id="clinic-country"
                  value={form.country}
                  onChange={(e) => updateForm({ country: e.target.value })}
                />
              </Field>
            </div>

            <div className="flex items-center gap-3 pt-2">
              <PrimaryButton type="submit" data-testid="clinic-save" busy={mutation.isPending}>
                {mutation.isPending ? "Saving…" : "Save profile"}
              </PrimaryButton>
              <button
                type="button"
                onClick={() => {
                  setForm(toForm(clinic));
                  setFormError(null);
                  setSaved(false);
                  mutation.reset();
                }}
                className="border border-border px-6 py-3 rounded-full font-bold text-xs hover:bg-muted transition-colors"
              >
                Reset
              </button>
            </div>
          </form>
        ) : null}
      </DataState>
    </SectionCard>
  );
}
