// @ts-check
// Status icons: the SHAPE differs per status (circle-check, triangle, octagon, dashed circle), so colour is
// never the only signal. All icons are decorative (aria-hidden); the text next to them carries the meaning.

const A = 'aria-hidden="true" focusable="false" width="16" height="16" viewBox="0 0 16 16"';

export const ICON = {
  green: `<svg ${A} class="ico"><circle cx="8" cy="8" r="6.5" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M5 8.2l2 2 4-4.4" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
  yellow: `<svg ${A} class="ico"><path d="M8 1.8L15 14H1z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/><path d="M8 6v3.6" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/><circle cx="8" cy="11.7" r="1" fill="currentColor"/></svg>`,
  red: `<svg ${A} class="ico"><path d="M5.2 1.5h5.6l3.7 3.7v5.6l-3.7 3.7H5.2l-3.7-3.7V5.2z" fill="currentColor"/><path d="M8 4.4v4.4" stroke="#fff" stroke-width="1.9" stroke-linecap="round"/><circle cx="8" cy="11.3" r="1.1" fill="#fff"/></svg>`,
  grey: `<svg ${A} class="ico"><circle cx="8" cy="8" r="6.3" fill="none" stroke="currentColor" stroke-width="1.6" stroke-dasharray="2.6 2.2"/><path d="M6.3 6.2a1.8 1.8 0 1 1 2.4 1.7c-.5.2-.7.5-.7 1v.4" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/><circle cx="8" cy="11.6" r=".9" fill="currentColor"/></svg>`,
  online: `<svg ${A} class="ico ico-s"><circle cx="8" cy="8" r="4" fill="currentColor"/></svg>`,
  offline: `<svg ${A} class="ico ico-s"><circle cx="8" cy="8" r="4.5" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M3 13L13 3" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>`,
  unknown: `<svg ${A} class="ico ico-s"><circle cx="8" cy="8" r="4.5" fill="none" stroke="currentColor" stroke-width="1.5" stroke-dasharray="2 2"/></svg>`,
  camera: `<svg ${A} class="ico ico-s"><rect x="1.5" y="4" width="9.5" height="8" rx="1.6" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M11 7l3.5-2v6L11 9z" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/></svg>`,
  cameraOff: `<svg ${A} class="ico ico-s"><rect x="1.5" y="4" width="9.5" height="8" rx="1.6" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M11 7l3.5-2v6L11 9z" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/><path d="M2 14L14 2" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>`,
  clock: `<svg ${A} class="ico ico-s"><circle cx="8" cy="8" r="6" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M8 4.6V8l2.4 1.6" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>`,
  episodes: `<svg ${A} class="ico ico-s"><path d="M3 3h10v10H3z" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M5.5 6h5M5.5 8.5h5M5.5 11h3" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>`,
  search: `<svg ${A} class="ico"><circle cx="7" cy="7" r="4.6" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M10.4 10.4L14 14" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>`,
  close: `<svg ${A} class="ico"><path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>`,
  pin: `<svg ${A} class="ico ico-s"><path d="M5 2h6l-1 4 2 2H4l2-2zM8 8v6" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/></svg>`,
};
