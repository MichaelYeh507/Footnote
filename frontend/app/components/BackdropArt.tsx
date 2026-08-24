/**
 * The backdrop, after the reference's actual architecture: a photograph as
 * the full-bleed base layer, with the animation as an effects layer OVER it —
 * three big soft mask openings sweep across the whole canvas (revealing
 * different regions of the photo) while the photo itself breathes with a
 * slow scale. All of it is CSS in globals.css under `.qa-photo`; motion is
 * disabled under prefers-reduced-motion.
 *
 * The image at /backdrop.avif is a LOCAL, GITIGNORED asset (an Unsplash+
 * preview the owner supplied) — the repo is public and third-party media is
 * never committed. Swap in a licensed or original photo at the same path
 * before shipping; everything else is treatment, not content.
 */
export function BackdropArt() {
  return (
    <div aria-hidden className="absolute inset-0 overflow-hidden">
      <div className="qa-photo" />
      {/* Keeps the headline band pale whatever the reveals are doing. */}
      <div className="qa-shield" />
    </div>
  );
}
