import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileText, Pencil, Plus } from "lucide-react";
import {
  consentTemplatesCreate,
  consentTemplatesList,
  consentTemplatesPartialUpdate,
  getConsentTemplatesListQueryKey,
  type ConsentTemplate,
  type TemplateApprovalStatusEnum,
} from "@workspace/api-client-react";
import { apiErrorFieldErrors, apiErrorMessage, toPage } from "@/lib/api";
import { DataState } from "@/components/common/DataState";
import {
  ErrorBanner,
  Field,
  Pill,
  PrimaryButton,
  SectionCard,
  SecondaryButton,
  SelectInput,
  TextInput,
} from "@/components/settings/settings-ui";

interface TemplateForm {
  key: string;
  name: string;
  purpose: string;
  locale: string;
  body: string;
  is_active: boolean;
  approval_state: TemplateApprovalStatusEnum;
}

const EMPTY_FORM: TemplateForm = {
  key: "",
  name: "",
  purpose: "",
  locale: "en",
  body: "",
  is_active: true,
  approval_state: "draft",
};

export function ConsentTemplatesSection() {
  const queryClient = useQueryClient();

  const templatesQuery = useQuery({
    queryKey: getConsentTemplatesListQueryKey(),
    queryFn: async () => toPage<ConsentTemplate>(await consentTemplatesList()).results,
    retry: false,
  });

  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<TemplateForm>(EMPTY_FORM);
  const [formError, setFormError] = useState<string | null>(null);

  function invalidate() {
    void queryClient.invalidateQueries({ queryKey: getConsentTemplatesListQueryKey() });
  }

  const createMutation = useMutation({
    mutationFn: (data: TemplateForm) =>
      consentTemplatesCreate({
        key: data.key,
        name: data.name,
        purpose: data.purpose,
        locale: data.locale,
        body: data.body,
        is_active: data.is_active,
        approval_state: data.approval_state,
      }),
    onSuccess: () => {
      setForm(EMPTY_FORM);
      setFormError(null);
      invalidate();
    },
    onError: (error) => setFormError(apiErrorMessage(error)),
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, data }: { id: string; data: TemplateForm }) =>
      consentTemplatesPartialUpdate(id, {
        key: data.key,
        name: data.name,
        purpose: data.purpose,
        locale: data.locale,
        body: data.body,
        is_active: data.is_active,
        approval_state: data.approval_state,
      }),
    onSuccess: () => {
      setEditingId(null);
      setForm(EMPTY_FORM);
      setFormError(null);
      invalidate();
    },
    onError: (error) => setFormError(apiErrorMessage(error)),
  });

  const fieldErrors = apiErrorFieldErrors(updateMutation.error ?? createMutation.error);
  const busy = createMutation.isPending || updateMutation.isPending;
  const templates = templatesQuery.data ?? [];

  function startEdit(template: ConsentTemplate) {
    setEditingId(template.id);
    setForm({
      key: template.key,
      name: template.name,
      purpose: template.purpose,
      locale: template.locale ?? "en",
      body: template.body,
      is_active: template.is_active ?? true,
      approval_state: template.approval_state ?? "draft",
    });
    setFormError(null);
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError(null);
    if (editingId) updateMutation.mutate({ id: editingId, data: form });
    else createMutation.mutate(form);
  }

  return (
    <SectionCard
      icon={FileText}
      title="Consent Templates"
      description="Reusable consent wording, versioned by the API on every edit."
    >
      <DataState
        loading={templatesQuery.isPending}
        error={templatesQuery.error}
        empty={templates.length === 0}
        emptyTitle="No consent templates"
        emptyMessage="Create the first template below."
        onRetry={() => void templatesQuery.refetch()}
      >
        <ul className="space-y-3" data-testid="templates-list">
          {templates.map((template) => (
            <li key={template.id} className="flex flex-wrap items-center justify-between gap-3 p-4 bg-muted/50 rounded-xl">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-bold text-foreground">{template.name}</span>
                  <Pill tone={template.approval_state === "approved" ? "success" : "warning"}>
                    {template.approval_state ?? "draft"}
                  </Pill>
                  <Pill tone={template.is_active === false ? "neutral" : "primary"}>
                    {template.is_active === false ? "inactive" : "active"}
                  </Pill>
                </div>
                <p className="text-xs text-muted-foreground mt-0.5">
                  <code className="font-mono">{template.key}</code> · {template.purpose} · v{template.version} ·{" "}
                  {template.locale ?? "en"}
                </p>
              </div>
              <SecondaryButton className="px-4 py-2" onClick={() => startEdit(template)} aria-label={`Edit ${template.name}`}>
                <Pencil size={12} /> Edit
              </SecondaryButton>
            </li>
          ))}
        </ul>
      </DataState>

      <form onSubmit={onSubmit} className="mt-6 border-t border-border/50 pt-6 space-y-4" noValidate>
        <p className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground">
          {editingId ? "Edit template" : "New template"}
        </p>
        {formError ? <ErrorBanner>{formError}</ErrorBanner> : null}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <Field label="Name" htmlFor="template-name" error={fieldErrors.name}>
            <TextInput
              id="template-name"
              data-testid="template-name"
              required
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
            />
          </Field>
          <Field label="Key" htmlFor="template-key" error={fieldErrors.key} hint="Stable identifier, e.g. booking_terms">
            <TextInput
              id="template-key"
              required
              value={form.key}
              onChange={(e) => setForm({ ...form, key: e.target.value })}
            />
          </Field>
          <Field label="Purpose" htmlFor="template-purpose" error={fieldErrors.purpose}>
            <TextInput
              id="template-purpose"
              required
              value={form.purpose}
              onChange={(e) => setForm({ ...form, purpose: e.target.value })}
            />
          </Field>
          <Field label="Locale" htmlFor="template-locale" error={fieldErrors.locale}>
            <TextInput
              id="template-locale"
              value={form.locale}
              onChange={(e) => setForm({ ...form, locale: e.target.value })}
            />
          </Field>
          <Field label="Approval state" htmlFor="template-approval" error={fieldErrors.approval_state}>
            <SelectInput
              id="template-approval"
              value={form.approval_state}
              onChange={(e) => setForm({ ...form, approval_state: e.target.value as TemplateApprovalStatusEnum })}
            >
              <option value="draft">draft</option>
              <option value="approved">approved</option>
            </SelectInput>
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
        <Field label="Body" htmlFor="template-body" error={fieldErrors.body}>
          <textarea
            id="template-body"
            required
            rows={5}
            value={form.body}
            onChange={(e) => setForm({ ...form, body: e.target.value })}
            className="w-full border border-border rounded-xl px-4 py-3 text-sm font-medium bg-white/70 text-foreground focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary transition-all"
          />
        </Field>
        <div className="flex items-center gap-3">
          <PrimaryButton type="submit" data-testid="template-save" busy={busy}>
            <Plus size={14} /> {editingId ? "Save template" : "Create template"}
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
