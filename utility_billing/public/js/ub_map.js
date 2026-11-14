// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
// "View on Map" for every record with a latitude and longitude (FS-MST-27). Opens OpenStreetMap; no key or account needed.
["Property", "Feeder", "Transformer", "Grid Station", "Common Area Facility", "Network Asset"].forEach((doctype) => {
	frappe.ui.form.on(doctype, {
		refresh(frm) {
			const { latitude: lat, longitude: lng } = frm.doc;
			if (!lat || !lng || frm.is_new()) return;
			frm.add_custom_button(__("View on Map"), () =>
				window.open(`https://www.openstreetmap.org/?mlat=${lat}&mlon=${lng}#map=18/${lat}/${lng}`, "_blank", "noopener")
			);
		},
	});
});
