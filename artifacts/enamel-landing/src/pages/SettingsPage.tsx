import { useState, type ReactElement } from "react";
import AppLayout from "@/layouts/AppLayout";
import { motion } from "framer-motion";
import { Building2, CalendarClock, FileText, MapPin, Plug, ScrollText, Users } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { ClinicProfileSection } from "@/components/settings/ClinicProfileSection";
import { LocationsSection } from "@/components/settings/LocationsSection";
import { MembersSection } from "@/components/settings/MembersSection";
import { ConsentTemplatesSection } from "@/components/settings/ConsentTemplatesSection";
import { BookingPolicySection } from "@/components/settings/BookingPolicySection";
import { ConnectorsSection } from "@/components/settings/ConnectorsSection";
import { AuditLogSection } from "@/components/settings/AuditLogSection";

interface SettingsTab {
  id: string;
  label: string;
  icon: LucideIcon;
  render: () => ReactElement;
}

const TABS: SettingsTab[] = [
  { id: "profile", label: "Clinic Profile", icon: Building2, render: () => <ClinicProfileSection /> },
  { id: "locations", label: "Locations", icon: MapPin, render: () => <LocationsSection /> },
  { id: "members", label: "Members", icon: Users, render: () => <MembersSection /> },
  { id: "consent", label: "Consent", icon: FileText, render: () => <ConsentTemplatesSection /> },
  { id: "booking", label: "Booking Policy", icon: CalendarClock, render: () => <BookingPolicySection /> },
  { id: "integrations", label: "Integrations", icon: Plug, render: () => <ConnectorsSection /> },
  { id: "audit", label: "Audit Log", icon: ScrollText, render: () => <AuditLogSection /> },
];

export default function SettingsPage() {
  const [activeTab, setActiveTab] = useState<string>("profile");
  const active = TABS.find((tab) => tab.id === activeTab) ?? TABS[0];

  return (
    <AppLayout>
      <div className="max-w-screen-2xl mx-auto space-y-8">
        <header>
          <span className="text-[10px] font-bold tracking-[0.2em] text-primary uppercase mb-2 block font-sans">
            Administration
          </span>
          <h2 className="text-4xl font-extrabold text-foreground font-display leading-none">Settings</h2>
        </header>

        <div
          role="tablist"
          aria-label="Settings sections"
          className="flex flex-wrap gap-2"
          data-testid="settings-tabs"
        >
          {TABS.map((tab) => {
            const Icon = tab.icon;
            const selected = tab.id === activeTab;
            return (
              <button
                key={tab.id}
                role="tab"
                type="button"
                aria-selected={selected}
                data-testid={`settings-tab-${tab.id}`}
                onClick={() => setActiveTab(tab.id)}
                className={`inline-flex items-center gap-2 px-5 py-2.5 rounded-full font-bold text-xs transition-all ${
                  selected
                    ? "bg-gradient-to-br from-primary to-primary-dim text-white shadow-lg shadow-primary/20"
                    : "border border-border text-foreground hover:bg-muted"
                }`}
              >
                <Icon size={14} />
                {tab.label}
              </button>
            );
          })}
        </div>

        <motion.div
          key={active.id}
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.25 }}
        >
          {active.render()}
        </motion.div>
      </div>
    </AppLayout>
  );
}
