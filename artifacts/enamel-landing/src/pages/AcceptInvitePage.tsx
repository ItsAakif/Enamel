import { useState } from "react";
import { Link, Redirect, useLocation } from "wouter";
import { CheckCircle2, MailCheck } from "lucide-react";
import { useMutation } from "@tanstack/react-query";
import { invitationsAcceptCreate, type InvitationAcceptedResponse } from "@workspace/api-client-react";
import { useAuth } from "@/hooks/use-auth";
import { apiErrorMessage } from "@/lib/api";

/**
 * Staff invitation acceptance (`/app/invitations/accept?token=...`).
 *
 * The accept endpoint requires an authenticated session, so an unauthenticated
 * visitor is bounced to `/login` with `next` pointing back here. The raw token
 * arrives in the query string (it is only ever in the email / the DEBUG API
 * response).
 */
export default function AcceptInvitePage() {
  const { isAuthenticated, isLoading, refresh } = useAuth();
  const [location] = useLocation();
  const [accepted, setAccepted] = useState<InvitationAcceptedResponse | null>(null);

  const token = typeof window === "undefined" ? "" : new URLSearchParams(window.location.search).get("token") ?? "";

  const mutation = useMutation({
    mutationFn: () => invitationsAcceptCreate(token),
    onSuccess: async (response) => {
      setAccepted(response);
      await refresh();
    },
  });

  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background" role="status" aria-busy="true">
        <span className="text-xs font-bold uppercase tracking-widest text-muted-foreground">Loading…</span>
      </div>
    );
  }

  if (!isAuthenticated) {
    return <Redirect to={`/login?next=${encodeURIComponent(location)}`} />;
  }

  return (
    <div
      className="min-h-screen flex items-center justify-center p-4"
      style={{ background: "linear-gradient(135deg, #f3f0ff 0%, #f9f6f5 40%, #f0f4ff 70%, #fdf4fb 100%)" }}
    >
      <div className="w-full max-w-md glass-card rounded-[2rem] p-10 shadow-xl">
        <span className="text-2xl font-black uppercase tracking-tighter text-foreground font-display">ENAMEL</span>

        {accepted ? (
          <div className="mt-6">
            <div className="flex items-center gap-2 text-emerald-600">
              <CheckCircle2 size={22} />
              <h1 className="text-2xl font-extrabold font-display text-foreground">Invitation accepted</h1>
            </div>
            <p className="text-sm text-muted-foreground mt-2">
              You have joined <span className="font-bold text-foreground">{accepted.clinic.name}</span> as{" "}
              <span className="font-bold text-foreground">{accepted.membership.role}</span>.
            </p>
            <Link
              href="/app/settings"
              className="mt-6 inline-flex bg-gradient-to-br from-primary to-primary-dim text-white px-6 py-3 rounded-full font-bold text-sm shadow-lg shadow-primary/20 hover:opacity-90 active:scale-95 transition-all"
            >
              Go to workspace
            </Link>
          </div>
        ) : (
          <div className="mt-6">
            <div className="flex items-center gap-2 text-primary">
              <MailCheck size={22} />
              <h1 className="text-2xl font-extrabold font-display text-foreground">Accept invitation</h1>
            </div>
            <p className="text-sm text-muted-foreground mt-2">
              {token
                ? "Accept the invitation sent to your email address."
                : "This page needs an invitation token. Open the link from your invitation email."}
            </p>

            {mutation.error ? (
              <div role="alert" className="mt-5 rounded-xl border border-destructive/20 bg-destructive/10 px-4 py-3 text-sm text-destructive font-medium">
                {apiErrorMessage(mutation.error)}
              </div>
            ) : null}

            <button
              type="button"
              data-testid="accept-invite-submit"
              disabled={token === "" || mutation.isPending}
              onClick={() => mutation.mutate()}
              className="mt-6 w-full bg-gradient-to-br from-primary to-primary-dim text-white py-3.5 rounded-xl font-bold text-sm shadow-lg shadow-primary/20 hover:opacity-90 active:scale-[0.99] transition-all disabled:opacity-60"
            >
              {mutation.isPending ? "Accepting…" : "Accept invitation"}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
