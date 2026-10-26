import { Suspense, lazy } from "react";
import { Switch, Route, Redirect, Router as WouterRouter } from "wouter";
import { PortalAuthProvider } from "@/hooks/use-portal-auth";

const LandingPage = lazy(() => import("@/pages/LandingPage"));
const LoginPage = lazy(() => import("@/pages/LoginPage"));
const GuidePage = lazy(() => import("@/pages/GuidePage"));
const RegisterPage = lazy(() => import("@/pages/RegisterPage"));
const ResetPasswordPage = lazy(() => import("@/pages/ResetPasswordPage"));
const AcceptInvitePage = lazy(() => import("@/pages/AcceptInvitePage"));
const PublicBookingPage = lazy(() => import("@/pages/PublicBookingPage"));
const PrivacyPage = lazy(() => import("@/pages/legal/PrivacyPage"));
const TermsPage = lazy(() => import("@/pages/legal/TermsPage"));
const AIEthicsPage = lazy(() => import("@/pages/legal/AIEthicsPage"));
const ContactPage = lazy(() => import("@/pages/legal/ContactPage"));
const DashboardPage = lazy(() => import("@/pages/DashboardPage"));
const SchedulingPage = lazy(() => import("@/pages/SchedulingPage"));
const PatientsPage = lazy(() => import("@/pages/PatientsPage"));
const PatientDetailPage = lazy(() => import("@/pages/PatientDetailPage"));
const DiagnosticsPage = lazy(() => import("@/pages/DiagnosticsPage"));
const AnalyticsPage = lazy(() => import("@/pages/AnalyticsPage"));
const VoiceAIPage = lazy(() => import("@/pages/VoiceAIPage"));
const ChartingPage = lazy(() => import("@/pages/ChartingPage"));
const BillingPage = lazy(() => import("@/pages/BillingPage"));
const AutomationsPage = lazy(() => import("@/pages/AutomationsPage"));
const SettingsPage = lazy(() => import("@/pages/SettingsPage"));
const PortalLoginPage = lazy(() => import("@/pages/portal/PortalLoginPage"));
const PortalHomePage = lazy(() => import("@/pages/portal/PortalHomePage"));
const PortalAppointmentsPage = lazy(() => import("@/pages/portal/PortalAppointmentsPage"));
const PortalRecordsPage = lazy(() => import("@/pages/portal/PortalRecordsPage"));
const PortalPlansPage = lazy(() => import("@/pages/portal/PortalPlansPage"));
const PortalInvoicesPage = lazy(() => import("@/pages/portal/PortalInvoicesPage"));
const PortalDocumentsPage = lazy(() => import("@/pages/portal/PortalDocumentsPage"));
const NotFound = lazy(() => import("@/pages/not-found"));

const Fallback = <div className="min-h-screen bg-background" />;

function Router() {
  return (
    <Suspense fallback={Fallback}>
      <Switch>
        {/* Public */}
        <Route path="/" component={LandingPage} />
        <Route path="/guide" component={GuidePage} />
        <Route path="/login" component={LoginPage} />
        <Route path="/register" component={RegisterPage} />
        <Route path="/reset-password" component={ResetPasswordPage} />
        <Route path="/book/:slug" component={PublicBookingPage} />

        {/* Public legal pages (the footer used to link to "#") */}
        <Route path="/legal/privacy" component={PrivacyPage} />
        <Route path="/legal/terms" component={TermsPage} />
        <Route path="/legal/ai-ethics" component={AIEthicsPage} />
        <Route path="/legal/contact" component={ContactPage} />

        {/* Patient portal (its own cookie + principal) */}
        <Route path="/portal/login" component={PortalLoginPage} />
        <Route path="/portal/appointments" component={PortalAppointmentsPage} />
        <Route path="/portal/records" component={PortalRecordsPage} />
        <Route path="/portal/plans" component={PortalPlansPage} />
        <Route path="/portal/invoices" component={PortalInvoicesPage} />
        <Route path="/portal/documents" component={PortalDocumentsPage} />
        <Route path="/portal" component={PortalHomePage} />

        {/* Staff workspace (guarded inside AppLayout) */}
        <Route path="/app/invitations/accept" component={AcceptInvitePage} />
        <Route path="/app/patients/:id" component={PatientDetailPage} />
        <Route path="/app/patients" component={PatientsPage} />
        <Route path="/app/scheduling" component={SchedulingPage} />
        <Route path="/app/charting" component={ChartingPage} />
        <Route path="/app/diagnostics" component={DiagnosticsPage} />
        <Route path="/app/voice-ai" component={VoiceAIPage} />
        <Route path="/app/billing" component={BillingPage} />
        <Route path="/app/analytics" component={AnalyticsPage} />
        <Route path="/app/automations" component={AutomationsPage} />
        <Route path="/app/settings" component={SettingsPage} />
        <Route path="/app" component={DashboardPage} />

        {/* Legacy top-level links */}
        <Route path="/dashboard">
          <Redirect to="/app" />
        </Route>
        <Route path="/scheduling">
          <Redirect to="/app/scheduling" />
        </Route>
        <Route path="/patients">
          <Redirect to="/app/patients" />
        </Route>
        <Route path="/diagnostics">
          <Redirect to="/app/diagnostics" />
        </Route>
        <Route path="/analytics">
          <Redirect to="/app/analytics" />
        </Route>
        <Route path="/voice-ai">
          <Redirect to="/app/voice-ai" />
        </Route>
        <Route path="/charting">
          <Redirect to="/app/charting" />
        </Route>
        <Route path="/billing">
          <Redirect to="/app/billing" />
        </Route>
        <Route path="/automations">
          <Redirect to="/app/automations" />
        </Route>
        <Route path="/settings">
          <Redirect to="/app/settings" />
        </Route>

        <Route component={NotFound} />
      </Switch>
    </Suspense>
  );
}

function App() {
  return (
    <WouterRouter base={import.meta.env.BASE_URL.replace(/\/$/, "")}>
      <PortalAuthProvider>
        <Router />
      </PortalAuthProvider>
    </WouterRouter>
  );
}

export default App;
