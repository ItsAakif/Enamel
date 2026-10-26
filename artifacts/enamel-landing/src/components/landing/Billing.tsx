import FeatureSplit from "@/components/landing/FeatureSplit";

export default function Billing() {
  return (
    <FeatureSplit
      eyebrow="Billing & Insurance"
      title="Billing that reconciles."
      mediaClassName="order-2 lg:order-1"
      copyClassName="order-1 lg:order-2"
      blobs={
        <div className="depth-blob w-[500px] h-[400px] top-[20%] left-[40%] opacity-15" style={{ background: "radial-gradient(circle, rgba(150,55,118,0.12) 0%, transparent 70%)" }} />
      }
      media={
        <div className="glass-card rounded-[2rem] p-8 shadow-2xl">
          <div className="flex justify-between items-center mb-6">
            <h4 className="font-display font-bold text-lg">Billing Overview</h4>
            <span className="text-xs font-bold text-emerald-600 bg-emerald-50 px-3 py-1.5 rounded-full">All Clear</span>
          </div>
          <div className="space-y-3">
            {[
              { id: "INV-2041", name: "Eleanor Vance", amount: "$1,250", status: "Paid", color: "bg-emerald-100 text-emerald-700" },
              { id: "INV-2042", name: "Marcus Chen", amount: "$150", status: "Pending", color: "bg-amber-100 text-amber-700" },
              { id: "INV-2043", name: "David G.", amount: "$850", status: "Authorized", color: "bg-sky-100 text-sky-700" },
            ].map((inv) => (
              <div key={inv.id} className="flex items-center justify-between p-4 bg-muted/50 rounded-xl">
                <div>
                  <p className="text-sm font-bold">{inv.name}</p>
                  <p className="text-[10px] text-muted-foreground">{inv.id}</p>
                </div>
                <div className="text-right">
                  <p className="text-sm font-black">{inv.amount}</p>
                  <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full ${inv.color}`}>{inv.status}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      }
      copy={
        <>
          <p className="text-xl text-muted-foreground leading-relaxed mb-10">
            Enamel raises invoices from accepted plan items, takes payments,
            issues refunds and reconciles gateway events. Amounts are held in
            integer minor units and every money movement is audited.
          </p>
          <div className="flex gap-8">
            <div>
              <div className="font-display text-4xl font-extrabold text-primary">Off</div>
              <div className="text-xs font-bold uppercase tracking-widest text-muted-foreground mt-1">Claims Module Disabled By Default</div>
            </div>
            <div>
              <div className="font-display text-4xl font-extrabold text-primary">Signed</div>
              <div className="text-xs font-bold uppercase tracking-widest text-muted-foreground mt-1">Gateway Webhooks Verified By HMAC</div>
            </div>
          </div>
        </>
      }
    />
  );
}
