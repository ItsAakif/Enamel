import { useMemo, useState, type FormEvent } from "react";
import { useParams } from "wouter";
import { useMutation, useQuery } from "@tanstack/react-query";
import { motion } from "framer-motion";
import { CalendarCheck, CheckCircle2, Clock, MapPin, Phone } from "lucide-react";
import {
  publicBookingsCreate,
  publicClinicsAvailabilityRetrieve,
  publicClinicsRetrieve,
  type AvailabilitySlot,
  type PublicBookingCreate,
} from "@workspace/api-client-react";
import { DataState } from "@/components/common/DataState";
import {
  apiErrorCode,
  apiErrorMessage,
  newIdempotencyKey,
  readBoolean,
  readNumber,
  readRecordArray,
  readString,
} from "@/lib/api";
import { formatDate, formatTime, isoDate } from "@/lib/format";

interface PublicService {
  id: string;
  name: string;
  duration: number | null;
  requiresOperatory: boolean;
}

interface PublicLocation {
  id: string;
  name: string;
}

const INPUT_CLASS =
  "w-full border border-border rounded-xl px-4 py-3 text-sm font-medium bg-white/70 focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary transition-all";

const CONSENT_PURPOSES = [
  { key: "booking_terms", label: "I agree to the booking terms and cancellation policy.", required: true },
  { key: "privacy", label: "I consent to my details being stored for treatment purposes.", required: true },
  { key: "marketing", label: "I would like to receive clinic updates (optional).", required: false },
] as const;

export default function PublicBookingPage() {
  const params = useParams<{ slug: string }>();
  const slug = params.slug ?? "";

  const clinicQuery = useQuery({
    queryKey: ["public-clinic", slug],
    queryFn: () => publicClinicsRetrieve(slug),
    enabled: slug !== "",
    retry: false,
  });

  const clinic = clinicQuery.data;

  const services: PublicService[] = useMemo(
    () =>
      readRecordArray(clinic, "appointment_types").map((item) => ({
        id: readString(item, "id"),
        name: readString(item, "name"),
        duration: readNumber(item, "duration_min"),
        requiresOperatory: readBoolean(item, "requires_operatory"),
      })),
    [clinic],
  );

  const locations: PublicLocation[] = useMemo(
    () =>
      readRecordArray(clinic, "locations").map((item) => ({
        id: readString(item, "id"),
        name: readString(item, "name"),
      })),
    [clinic],
  );

  const providers = useMemo(
    () => readRecordArray(clinic, "providers").map((item) => readString(item, "display_name")),
    [clinic],
  );

  const [serviceId, setServiceId] = useState("");
  const [locationId, setLocationId] = useState("");
  const [selectedSlot, setSelectedSlot] = useState<AvailabilitySlot | null>(null);
  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [dateOfBirth, setDateOfBirth] = useState("");
  const [notes, setNotes] = useState("");
  const [website, setWebsite] = useState(""); // honeypot
  const [consents, setConsents] = useState<Record<string, boolean>>({ booking_terms: false, privacy: false, marketing: false });
  const [formError, setFormError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string[]>>({});
  const [confirmed, setConfirmed] = useState<{ reference: string; startsAt: string } | null>(null);

  const today = isoDate(new Date());
  const horizon = isoDate(new Date(Date.now() + 14 * 86_400_000));

  const availabilityQuery = useQuery({
    queryKey: ["public-availability", slug, serviceId, locationId, today, horizon],
    queryFn: () =>
      publicClinicsAvailabilityRetrieve(slug, {
        type: serviceId,
        from: today,
        to: horizon,
        ...(locationId ? { location: locationId } : {}),
      }),
    enabled: slug !== "" && serviceId !== "",
    retry: false,
  });

  const slots: AvailabilitySlot[] = useMemo(() => {
    const raw = readRecordArray(availabilityQuery.data, "slots");
    return raw
      .map((item) => ({
        start: readString(item, "start"),
        end: readString(item, "end"),
        reason: readString(item, "reason"),
        provider: readString(item, "provider") || null,
        provider_name: readString(item, "provider_name") || null,
        operatory: readString(item, "operatory") || null,
        operatory_name: readString(item, "operatory_name") || null,
        location: readString(item, "location") || null,
      }))
      .filter((slot) => slot.start !== "");
  }, [availabilityQuery.data]);

  const bookingMutation = useMutation({
    mutationFn: () => {
      const payload: PublicBookingCreate = {
        appointment_type: serviceId,
        slot_start: selectedSlot ? selectedSlot.start : "",
        ...(locationId ? { location: locationId } : {}),
        patient: {
          first_name: firstName.trim(),
          last_name: lastName.trim(),
          phone: phone.trim(),
          email: email.trim(),
          date_of_birth: dateOfBirth || null,
          notes: notes.trim(),
        },
        consents: {
          booking_terms: Boolean(consents.booking_terms),
          privacy: Boolean(consents.privacy),
          marketing: Boolean(consents.marketing),
        },
        idempotency_key: newIdempotencyKey(),
        website,
      };
      return publicBookingsCreate(payload);
    },
  });

  function fieldError(name: string) {
    const messages = fieldErrors[name];
    if (!messages || messages.length === 0) return null;
    return <p className="text-xs text-destructive mt-1">{messages.join(" ")}</p>;
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError(null);
    setFieldErrors({});
    if (!selectedSlot) {
      setFormError("Choose an available time before booking.");
      return;
    }
    if (!consents.booking_terms || !consents.privacy) {
      setFormError("Please accept the booking terms and privacy consent.");
      return;
    }
    try {
      const result = await bookingMutation.mutateAsync();
      setConfirmed({ reference: result.reference, startsAt: result.starts_at });
    } catch (error) {
      const code = apiErrorCode(error);
      if (code === "slot_conflict") {
        setFormError("That slot was just taken. Please pick another time.");
        setSelectedSlot(null);
        await availabilityQuery.refetch();
      } else if (code === "hold_expired") {
        setFormError("The previous hold on this slot expired. Please pick another time.");
        setSelectedSlot(null);
        await availabilityQuery.refetch();
      } else if (code === "throttled") {
        setFormError("Too many booking attempts. Please wait a minute and try again.");
      } else {
        setFormError(apiErrorMessage(error));
      }
    }
  }

  if (clinicQuery.isError) {
    return (
      <div className="min-h-screen flex items-center justify-center p-4" style={{ background: "linear-gradient(135deg, #f3f0ff 0%, #f9f6f5 40%, #f0f4ff 70%, #fdf4fb 100%)" }}>
        <div className="max-w-md w-full">
          <DataState error={clinicQuery.error} onRetry={() => void clinicQuery.refetch()}>
            <div />
          </DataState>
        </div>
      </div>
    );
  }

  return (
    <div
      className="min-h-screen p-4 md:p-10"
      style={{ background: "linear-gradient(135deg, #f3f0ff 0%, #f9f6f5 40%, #f0f4ff 70%, #fdf4fb 100%)" }}
    >
      <div className="max-w-3xl mx-auto space-y-8">
        <header className="text-center">
          <span className="text-2xl font-black uppercase tracking-tighter text-foreground font-display">ENAMEL</span>
          <h1 className="text-4xl font-extrabold font-display text-foreground mt-4">
            {clinic?.name ?? "Book an appointment"}
          </h1>
          <p className="text-sm text-muted-foreground mt-2">Book online in a few steps.</p>
        </header>

        <DataState loading={clinicQuery.isPending} error={clinicQuery.error} onRetry={() => void clinicQuery.refetch()}>
          {clinic ? (
            <>
              <div className="glass-card rounded-[2rem] p-8 shadow-sm space-y-4">
                <div className="flex flex-wrap gap-6 text-sm text-muted-foreground">
                  {clinic.phone ? (
                    <span className="inline-flex items-center gap-2">
                      <Phone size={14} /> {clinic.phone}
                    </span>
                  ) : null}
                  <span className="inline-flex items-center gap-2">
                    <Clock size={14} /> {clinic.timezone}
                  </span>
                  {providers.length > 0 ? (
                    <span className="inline-flex items-center gap-2">
                      <CalendarCheck size={14} /> {providers.join(", ")}
                    </span>
                  ) : null}
                </div>
                {locations.length > 0 ? (
                  <div className="flex flex-wrap gap-2 pt-2 border-t border-border">
                    {locations.map((location) => (
                      <span key={location.id} className="inline-flex items-center gap-2 text-xs font-bold text-foreground bg-muted px-3 py-1.5 rounded-lg">
                        <MapPin size={12} /> {location.name}
                      </span>
                    ))}
                  </div>
                ) : null}
              </div>

              {confirmed ? (
                <motion.div
                  initial={{ opacity: 0, y: 12 }}
                  animate={{ opacity: 1, y: 0 }}
                  className="glass-card rounded-[2rem] p-10 shadow-sm text-center"
                  role="status"
                >
                  <CheckCircle2 size={40} className="text-emerald-500 mx-auto" />
                  <h2 className="text-2xl font-bold font-display mt-4">Booking confirmed</h2>
                  <p className="text-sm text-muted-foreground mt-2">
                    Your appointment starts {formatDate(confirmed.startsAt)} at {formatTime(confirmed.startsAt)}.
                  </p>
                  <p className="mt-4 text-xs font-bold uppercase tracking-widest text-muted-foreground">Reference</p>
                  <p className="text-lg font-black font-display text-primary" data-testid="booking-reference">
                    {confirmed.reference}
                  </p>
                </motion.div>
              ) : (
                <form onSubmit={onSubmit} className="space-y-6" noValidate>
                  <div className="glass-card rounded-[2rem] p-8 shadow-sm space-y-5">
                    <h2 className="text-lg font-bold font-display">1. Choose a service</h2>
                    {services.length === 0 ? (
                      <p className="text-sm text-muted-foreground">This clinic has no online-bookable services.</p>
                    ) : (
                      <div className="flex flex-wrap gap-2">
                        {services.map((service) => (
                          <button
                            key={service.id}
                            type="button"
                            aria-pressed={serviceId === service.id}
                            onClick={() => {
                              setServiceId(service.id);
                              setSelectedSlot(null);
                            }}
                            className={`px-4 py-3 rounded-xl text-xs font-bold border transition-colors ${
                              serviceId === service.id
                                ? "bg-primary text-white border-primary"
                                : "border-border text-foreground hover:border-primary/50"
                            }`}
                          >
                            {service.name}
                            {service.duration ? ` · ${service.duration} min` : ""}
                          </button>
                        ))}
                      </div>
                    )}
                    {locations.length > 0 ? (
                      <div>
                        <label htmlFor="booking-location" className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground block mb-2">
                          Location (optional)
                        </label>
                        <select
                          id="booking-location"
                          value={locationId}
                          onChange={(event) => {
                            setLocationId(event.target.value);
                            setSelectedSlot(null);
                          }}
                          className={INPUT_CLASS}
                        >
                          <option value="">Any location</option>
                          {locations.map((location) => (
                            <option key={location.id} value={location.id}>
                              {location.name}
                            </option>
                          ))}
                        </select>
                      </div>
                    ) : null}
                  </div>

                  <div className="glass-card rounded-[2rem] p-8 shadow-sm space-y-5">
                    <h2 className="text-lg font-bold font-display">2. Pick a time</h2>
                    {serviceId === "" ? (
                      <p className="text-sm text-muted-foreground">Select a service to see available times.</p>
                    ) : (
                      <DataState
                        loading={availabilityQuery.isPending}
                        error={availabilityQuery.error}
                        empty={slots.length === 0}
                        emptyTitle="No open times"
                        emptyMessage="There are no bookable slots in the next two weeks. Try another service or location."
                        onRetry={() => void availabilityQuery.refetch()}
                      >
                        <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 max-h-64 overflow-y-auto">
                          {slots.map((slot) => (
                            <button
                              key={`${slot.start}-${slot.provider ?? ""}`}
                              type="button"
                              aria-pressed={selectedSlot?.start === slot.start}
                              onClick={() => setSelectedSlot(slot)}
                              className={`min-h-[44px] px-3 py-2 rounded-xl text-xs font-bold border transition-colors ${
                                selectedSlot?.start === slot.start
                                  ? "bg-primary text-white border-primary"
                                  : "border-border text-foreground hover:border-primary/50"
                              }`}
                            >
                              {formatDate(slot.start)} · {formatTime(slot.start)}
                            </button>
                          ))}
                        </div>
                      </DataState>
                    )}
                  </div>

                  <div className="glass-card rounded-[2rem] p-8 shadow-sm space-y-5">
                    <h2 className="text-lg font-bold font-display">3. Your details</h2>

                    {formError ? (
                      <div role="alert" className="rounded-xl border border-destructive/20 bg-destructive/10 px-4 py-3 text-sm text-destructive">
                        {formError}
                      </div>
                    ) : null}

                    <div className="grid grid-cols-2 gap-4">
                      <div>
                        <label htmlFor="booking-first" className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground block mb-2">
                          First name
                        </label>
                        <input id="booking-first" required value={firstName} onChange={(e) => setFirstName(e.target.value)} className={INPUT_CLASS} />
                        {fieldError("patient.first_name")}
                      </div>
                      <div>
                        <label htmlFor="booking-last" className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground block mb-2">
                          Last name
                        </label>
                        <input id="booking-last" required value={lastName} onChange={(e) => setLastName(e.target.value)} className={INPUT_CLASS} />
                        {fieldError("patient.last_name")}
                      </div>
                    </div>

                    <div className="grid grid-cols-2 gap-4">
                      <div>
                        <label htmlFor="booking-phone" className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground block mb-2">
                          Phone
                        </label>
                        <input id="booking-phone" required value={phone} onChange={(e) => setPhone(e.target.value)} className={INPUT_CLASS} />
                        {fieldError("patient.phone")}
                      </div>
                      <div>
                        <label htmlFor="booking-email" className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground block mb-2">
                          Email
                        </label>
                        <input id="booking-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} className={INPUT_CLASS} />
                        {fieldError("patient.email")}
                      </div>
                    </div>

                    <div className="grid grid-cols-2 gap-4">
                      <div>
                        <label htmlFor="booking-dob" className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground block mb-2">
                          Date of birth
                        </label>
                        <input id="booking-dob" type="date" value={dateOfBirth} onChange={(e) => setDateOfBirth(e.target.value)} className={INPUT_CLASS} />
                        {fieldError("patient.date_of_birth")}
                      </div>
                    </div>

                    <div>
                      <label htmlFor="booking-notes" className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground block mb-2">
                        Notes
                      </label>
                      <textarea id="booking-notes" rows={3} value={notes} onChange={(e) => setNotes(e.target.value)} className={`${INPUT_CLASS} resize-none`} />
                    </div>

                    {/* Honeypot: hidden from humans, filled by bots. */}
                    <div className="hidden" aria-hidden="true">
                      <label htmlFor="booking-website">Website</label>
                      <input id="booking-website" tabIndex={-1} autoComplete="off" value={website} onChange={(e) => setWebsite(e.target.value)} />
                    </div>

                    <div className="space-y-3 pt-2">
                      {CONSENT_PURPOSES.map((consent) => (
                        <label key={consent.key} className="flex items-start gap-3 text-sm text-foreground cursor-pointer">
                          <input
                            type="checkbox"
                            className="mt-0.5 w-4 h-4 accent-primary"
                            checked={Boolean(consents[consent.key])}
                            onChange={(e) => setConsents((s) => ({ ...s, [consent.key]: e.target.checked }))}
                          />
                          <span>
                            {consent.label}
                            {consent.required ? <span className="text-destructive"> *</span> : null}
                          </span>
                        </label>
                      ))}
                    </div>

                    <button
                      type="submit"
                      data-testid="booking-submit"
                      disabled={bookingMutation.isPending}
                      className="w-full bg-gradient-to-br from-primary to-primary-dim text-white py-3.5 rounded-xl font-bold text-sm shadow-lg shadow-primary/20 hover:opacity-90 active:scale-[0.99] transition-all disabled:opacity-60"
                    >
                      {bookingMutation.isPending ? "Booking…" : "Confirm booking"}
                    </button>
                  </div>
                </form>
              )}
            </>
          ) : null}
        </DataState>
      </div>
    </div>
  );
}
