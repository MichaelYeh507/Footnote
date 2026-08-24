import { AskSurface } from "./components/AskSurface";
import { BackdropArt } from "./components/BackdropArt";
import { PillNav } from "./components/PillNav";

/**
 * The front door: grounded Q&A over the 10-K corpus, in the reference
 * design's language — a rounded canvas on a soft field, a floating pill
 * nav, one giant prompt, drifting color behind it. The extraction grid
 * lives at /extractions.
 */
export default function Home() {
  return (
    <div className="min-h-screen bg-zinc-200/70 p-3 font-sans md:p-5">
      <div className="relative flex min-h-[calc(100vh-1.5rem)] flex-col overflow-hidden rounded-3xl bg-[#f7f8f8] shadow-sm md:min-h-[calc(100vh-2.5rem)]">
        {/* Art layer + animation layer; see BackdropArt. */}
        <BackdropArt />

        <PillNav active="ask" />
        <div className="relative flex flex-1 flex-col">
          <AskSurface />
        </div>
      </div>
    </div>
  );
}
