// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
// Shared line icons for the custom pages (dashboard, recovery). Loaded before a page's own script by its loader.

window.UB_ICONS = {
	billed: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h6"/>',
	collected: '<path d="M3 7a2 2 0 0 1 2-2h13v4"/><path d="M3 7v11a2 2 0 0 0 2 2h14V9H5a2 2 0 0 1-2-2z"/><circle cx="16" cy="14.5" r="1"/>',
	arrears: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
	losses: '<path d="M13 2 4 14h7l-1 8 9-12h-7z"/>',
	units: '<path d="M12 14l4-4"/><path d="M3.3 17a9 9 0 1 1 17.4 0"/>',
	defaulters: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><path d="M16 4.6a3.5 3.5 0 0 1 0 6.8M18.5 14a6.5 6.5 0 0 1 3 6"/>',
	meters: '<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3"/>',
	service: '<path d="M14.7 6.3a4 4 0 0 0-5 5L3 18l3 3 6.7-6.7a4 4 0 0 0 5-5l-2.5 2.5-2.4-.6-.6-2.4z"/>',
	alert: '<path d="M12 3 2 20h20z"/><path d="M12 10v4M12 17h.01"/>',
	info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
	check: '<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>',
	plus: '<path d="M12 5v14M5 12h14"/>',
	receipt: '<path d="M7 3h10l2 3v15l-3-2-2 2-2-2-2 2-2-2-3 2V6z"/><path d="M9 9h6M9 13h6"/>',
	calendar: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>',
	bolt: '<path d="M13 2 4 14h7l-1 8 9-12h-7z"/>',
	chart: '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
};
window.ub_icon = (name) =>
	`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${UB_ICONS[name] || ""}</svg>`;
