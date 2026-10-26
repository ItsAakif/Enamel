import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Mail, ShieldCheck, UserPlus, Users } from "lucide-react";
import {
  clinicsInvitationsCreate,
  clinicsInvitationsList,
  clinicsInvitationsRevokeCreate,
  clinicsMembersDestroy,
  clinicsMembersList,
  clinicsMembersPartialUpdate,
  getClinicsInvitationsListQueryKey,
  getClinicsMembersListQueryKey,
  type Invitation,
  type Membership,
  type MembershipRoleEnum,
  type MembershipUpdateStatusEnum,
} from "@workspace/api-client-react";
import { useAuth } from "@/hooks/use-auth";
import { apiErrorFieldErrors, apiErrorMessage, readString, toPage } from "@/lib/api";
import { formatDate, initials } from "@/lib/format";
import { DataState } from "@/components/common/DataState";
import {
  DangerButton,
  ErrorBanner,
  Field,
  Pill,
  PrimaryButton,
  SectionCard,
  SecondaryButton,
  SelectInput,
  TextInput,
} from "@/components/settings/settings-ui";

const ROLES: MembershipRoleEnum[] = ["owner", "dentist", "receptionist", "assistant"];

const STATUS_TONE: Record<string, "success" | "warning" | "neutral" | "danger"> = {
  active: "success",
  invited: "warning",
  suspended: "danger",
  removed: "neutral",
};

export function MembersSection() {
  const { activeClinicId, user } = useAuth();
  const queryClient = useQueryClient();
  const clinicId = activeClinicId ?? "";

  const membersQuery = useQuery({
    queryKey: getClinicsMembersListQueryKey(clinicId),
    queryFn: async () => toPage<Membership>(await clinicsMembersList(clinicId)).results,
    enabled: activeClinicId !== null,
    retry: false,
  });

  const invitationsQuery = useQuery({
    queryKey: getClinicsInvitationsListQueryKey(clinicId),
    queryFn: async () => toPage<Invitation>(await clinicsInvitationsList(clinicId)).results,
    enabled: activeClinicId !== null,
    retry: false,
  });

  const [email, setEmail] = useState("");
  const [inviteRole, setInviteRole] = useState<MembershipRoleEnum>("receptionist");
  const [inviteError, setInviteError] = useState<string | null>(null);
  const [inviteToken, setInviteToken] = useState<string | null>(null);
  const [rowError, setRowError] = useState<string | null>(null);

  function invalidate() {
    void queryClient.invalidateQueries({ queryKey: getClinicsMembersListQueryKey(clinicId) });
    void queryClient.invalidateQueries({ queryKey: getClinicsInvitationsListQueryKey(clinicId) });
  }

  const inviteMutation = useMutation({
    mutationFn: () => clinicsInvitationsCreate(clinicId, { email, role: inviteRole }),
    onSuccess: (invitation) => {
      setInviteError(null);
      setInviteToken(readString(invitation, "token") || null);
      setEmail("");
      invalidate();
    },
    onError: (error) => {
      setInviteToken(null);
      setInviteError(apiErrorMessage(error));
    },
  });

  const revokeMutation = useMutation({
    mutationFn: (invitationId: string) => clinicsInvitationsRevokeCreate(clinicId, invitationId),
    onSuccess: () => {
      setRowError(null);
      invalidate();
    },
    onError: (error) => setRowError(apiErrorMessage(error)),
  });

  const roleMutation = useMutation({
    mutationFn: ({ id, role }: { id: string; role: MembershipRoleEnum }) =>
      clinicsMembersPartialUpdate(clinicId, id, { role }),
    onSuccess: () => {
      setRowError(null);
      invalidate();
    },
    onError: (error) => setRowError(apiErrorMessage(error)),
  });

  const statusMutation = useMutation({
    mutationFn: ({ id, status }: { id: string; status: MembershipUpdateStatusEnum }) =>
      clinicsMembersPartialUpdate(clinicId, id, { status }),
    onSuccess: () => {
      setRowError(null);
      invalidate();
    },
    onError: (error) => setRowError(apiErrorMessage(error)),
  });

  const removeMutation = useMutation({
    mutationFn: (id: string) => clinicsMembersDestroy(clinicId, id),
    onSuccess: () => {
      setRowError(null);
      invalidate();
    },
    onError: (error) => setRowError(apiErrorMessage(error)),
  });

  const inviteFieldErrors = apiErrorFieldErrors(inviteMutation.error);
  const members = membersQuery.data ?? [];
  const invitations = invitationsQuery.data ?? [];

  function onInvite(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setInviteError(null);
    setInviteToken(null);
    inviteMutation.mutate();
  }

  return (
    <SectionCard icon={Users} title="Members & Access" description="Team roles, invitations and account status.">
      {rowError ? <ErrorBanner>{rowError}</ErrorBanner> : null}

      <DataState
        loading={membersQuery.isPending}
        error={membersQuery.error}
        empty={members.length === 0}
        emptyTitle="No members"
        emptyMessage="Invite the first team member below."
        onRetry={() => void membersQuery.refetch()}
      >
        <ul className="space-y-3" data-testid="members-list">
          {members.map((member) => {
            const isSelf = user !== null && member.user.id === user.id;
            const busyRow =
              (roleMutation.isPending && roleMutation.variables?.id === member.id) ||
              (statusMutation.isPending && statusMutation.variables?.id === member.id) ||
              (removeMutation.isPending && removeMutation.variables === member.id);
            return (
              <li key={member.id} className="p-4 bg-muted/50 rounded-xl space-y-3">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="flex items-center gap-3 min-w-0">
                    <div className="w-9 h-9 rounded-full bg-gradient-to-br from-primary to-primary-dim flex items-center justify-center text-white text-xs font-black shrink-0">
                      {initials(member.user.full_name || member.user.email)}
                    </div>
                    <div className="min-w-0">
                      <p className="text-sm font-bold text-foreground truncate">
                        {member.user.full_name || member.user.email}
                        {isSelf ? <span className="text-muted-foreground font-normal"> (you)</span> : null}
                      </p>
                      <p className="text-xs text-muted-foreground truncate">{member.user.email}</p>
                    </div>
                  </div>
                  <Pill tone={STATUS_TONE[member.status] ?? "neutral"}>{member.status}</Pill>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <label className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground" htmlFor={`role-${member.id}`}>
                    Role
                  </label>
                  <SelectInput
                    id={`role-${member.id}`}
                    aria-label={`Role for ${member.user.email}`}
                    value={member.role}
                    disabled={busyRow}
                    onChange={(e) =>
                      roleMutation.mutate({ id: member.id, role: e.target.value as MembershipRoleEnum })
                    }
                    className="w-auto py-2"
                  >
                    {ROLES.map((role) => (
                      <option key={role} value={role}>
                        {role}
                      </option>
                    ))}
                  </SelectInput>
                  {member.status === "suspended" ? (
                    <SecondaryButton
                      className="px-4 py-2"
                      busy={busyRow}
                      onClick={() => statusMutation.mutate({ id: member.id, status: "active" })}
                    >
                      Reactivate
                    </SecondaryButton>
                  ) : (
                    <SecondaryButton
                      className="px-4 py-2"
                      busy={busyRow}
                      onClick={() => statusMutation.mutate({ id: member.id, status: "suspended" })}
                    >
                      Suspend
                    </SecondaryButton>
                  )}
                  <DangerButton
                    className="px-4 py-2"
                    disabled={isSelf}
                    busy={busyRow}
                    onClick={() => removeMutation.mutate(member.id)}
                    aria-label={`Remove ${member.user.email}`}
                  >
                    Remove
                  </DangerButton>
                </div>
              </li>
            );
          })}
        </ul>
      </DataState>

      <div className="mt-6 border-t border-border/50 pt-6 space-y-4">
        <p className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground flex items-center gap-2">
          <UserPlus size={12} /> Invite a member
        </p>
        {inviteError ? <ErrorBanner>{inviteError}</ErrorBanner> : null}
        {inviteToken ? (
          <div role="status" className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-xs text-amber-800">
            <p className="font-bold">Development convenience: invitation token</p>
            <code className="font-mono break-all">{inviteToken}</code>
            <p className="mt-1">
              Accept link: <code className="font-mono break-all">/app/invitations/accept?token={inviteToken}</code>
            </p>
          </div>
        ) : null}
        <form onSubmit={onInvite} className="flex flex-wrap items-end gap-3" noValidate>
          <div className="flex-1 min-w-[220px]">
            <Field label="Email" htmlFor="invite-email" error={inviteFieldErrors.email}>
              <TextInput
                id="invite-email"
                data-testid="invite-email"
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="teammate@clinic.com"
              />
            </Field>
          </div>
          <div>
            <Field label="Role" htmlFor="invite-role" error={inviteFieldErrors.role}>
              <SelectInput
                id="invite-role"
                data-testid="invite-role"
                value={inviteRole}
                onChange={(e) => setInviteRole(e.target.value as MembershipRoleEnum)}
                className="w-auto"
              >
                {ROLES.map((role) => (
                  <option key={role} value={role}>
                    {role}
                  </option>
                ))}
              </SelectInput>
            </Field>
          </div>
          <PrimaryButton type="submit" data-testid="invite-submit" busy={inviteMutation.isPending} className="mb-0.5">
            <Mail size={14} /> Send invite
          </PrimaryButton>
        </form>

        <div>
          <p className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground mb-3 flex items-center gap-2">
            <ShieldCheck size={12} /> Pending invitations
          </p>
          {invitationsQuery.isPending ? (
            <p className="text-xs text-muted-foreground">Loading invitations…</p>
          ) : invitationsQuery.error ? (
            <p className="text-xs text-destructive">{apiErrorMessage(invitationsQuery.error)}</p>
          ) : invitations.length === 0 ? (
            <p className="text-xs text-muted-foreground">No pending invitations.</p>
          ) : (
            <ul className="space-y-2" data-testid="invitations-list">
              {invitations.map((invitation) => (
                <li
                  key={invitation.id}
                  className="flex flex-wrap items-center justify-between gap-3 p-3 bg-muted/50 rounded-xl"
                >
                  <div className="min-w-0">
                    <p className="text-sm font-bold text-foreground truncate">{invitation.email}</p>
                    <p className="text-xs text-muted-foreground">
                      {invitation.role} · expires {formatDate(invitation.expires_at)}
                    </p>
                  </div>
                  <DangerButton
                    className="px-4 py-2"
                    busy={revokeMutation.isPending && revokeMutation.variables === invitation.id}
                    onClick={() => revokeMutation.mutate(invitation.id)}
                    aria-label={`Revoke invitation for ${invitation.email}`}
                  >
                    Revoke
                  </DangerButton>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </SectionCard>
  );
}
