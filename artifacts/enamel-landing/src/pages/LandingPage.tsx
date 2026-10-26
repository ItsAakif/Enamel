import ResizableNavbar from "@/components/ResizableNavbar";
import Hero from "@/components/landing/Hero";
import SocialProof from "@/components/landing/SocialProof";
import Transformation from "@/components/landing/Transformation";
import VoiceAI from "@/components/landing/VoiceAI";
import ImagingReview from "@/components/landing/ImagingReview";
import Scheduling from "@/components/landing/Scheduling";
import Billing from "@/components/landing/Billing";
import Emr from "@/components/landing/Emr";
import Testimonials from "@/components/landing/Testimonials";
import FinalCTA from "@/components/landing/FinalCTA";
import Footer from "@/components/landing/Footer";

export default function LandingPage() {
  return (
    <div className="min-h-screen bg-background font-sans overflow-x-hidden selection:bg-primary/20">
      <ResizableNavbar />

      <Hero />
      <SocialProof />
      <Transformation />
      <VoiceAI />
      <ImagingReview />
      <Scheduling />
      <Billing />
      <Emr />
      <Testimonials />
      <FinalCTA />
      <Footer />
    </div>
  );
}
