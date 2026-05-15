-- Seed data for local development and CI.
-- Re-runnable: existing system rows are cleared before reload.
-- Generated from remote on 2026-05-07.
-- 21 system categories + 40 service_templates.

BEGIN;
SET LOCAL session_replication_role = replica;

DELETE FROM "public"."service_templates";
DELETE FROM "public"."categories" WHERE "is_system" = true;

-- Categories first (service_templates references categories.id)
INSERT INTO "public"."categories" ("id", "user_id", "name", "category_type", "icon", "color", "parent_id", "is_system", "display_order", "created_at", "is_editable", "is_visible", "clerk_user_id") VALUES
	('8f2ca835-0da7-4eae-b206-c11b895f491e', NULL, 'Bonus', 'income', '🎉', '#064E3B', NULL, true, 7, '2025-12-28 16:52:17.237595+00', true, true, NULL),
	('a24ddfb6-f5bf-47eb-8a90-cdeb03b196ec', NULL, 'Freelance', 'income', '💻', '#34D399', NULL, true, 2, '2025-12-28 16:52:17.237595+00', true, true, NULL),
	('5c2f76bf-8b71-4714-9489-8d6ae4fde3ed', NULL, 'Insurance', 'expense', '🛡️', '#A855F7', NULL, true, 10, '2025-12-28 16:52:17.237595+00', true, true, NULL),
	('510d9dfa-0d7f-49eb-b3cf-27cab9545b01', NULL, 'Other Income', 'income', '💰', '#A7F3D0', NULL, true, 99, '2025-12-28 16:52:17.237595+00', true, true, NULL),
	('c8bfd81a-6db4-4020-8446-20e7d51bbf63', NULL, 'Shopping', 'expense', '🛍️', '#5B21B6', NULL, true, 6, '2025-12-28 16:52:17.237595+00', true, true, NULL),
	('3ff55a2f-2426-4d5a-8406-f9382da6d819', NULL, 'Transport', 'expense', '🚗', '#A78BFA', NULL, true, 3, '2025-12-28 16:52:17.237595+00', true, true, NULL),
	('bafc42c8-cc96-438c-a55c-dfc24680d60c', NULL, 'Subscriptions', 'expense', '📱', '#C084FC', NULL, true, 11, '2025-12-28 16:52:17.237595+00', true, true, NULL),
	('4e2fb2ce-1762-4176-96e2-f6605aba1fbe', NULL, 'Gifts', 'income', '🎁', '#047857', NULL, true, 5, '2025-12-28 16:52:17.237595+00', true, true, NULL),
	('ffaf78d6-7c1f-4f9c-a378-b49bce4c1dbc', NULL, 'Rental Income', 'income', '🏘️', '#059669', NULL, true, 4, '2025-12-28 16:52:17.237595+00', true, true, NULL),
	('5e59de9f-e6a2-4b99-b9eb-dd9a3135ea6d', NULL, 'Salary', 'income', '💼', '#10B981', NULL, true, 1, '2025-12-28 16:52:17.237595+00', true, true, NULL),
	('3da6bbde-61d8-4154-9757-8f368518a9a1', NULL, 'Investments', 'income', '📈', '#6EE7B7', NULL, true, 3, '2025-12-28 16:52:17.237595+00', true, true, NULL),
	('3a856fef-dbc3-45a7-abba-e1d862a24341', NULL, 'Personal Care', 'expense', '💅', '#9333EA', NULL, true, 9, '2025-12-28 16:52:17.237595+00', true, true, NULL),
	('5008e34e-b57e-47d7-8e04-6ca7da4ef06a', NULL, 'Food & Dining', 'expense', '🍔', '#8B5CF6', NULL, true, 2, '2025-12-28 16:52:17.237595+00', true, true, NULL),
	('68fd4a41-9e07-4342-9655-4bc256dd60d4', NULL, 'Education', 'expense', '📚', '#7E22CE', NULL, true, 8, '2025-12-28 16:52:17.237595+00', true, true, NULL),
	('8079e9fa-5566-4b86-b7dd-dea8076badfd', NULL, 'Entertainment', 'expense', '🎬', '#6D28D9', NULL, true, 5, '2025-12-28 16:52:17.237595+00', true, true, NULL),
	('24b4edb6-3b43-446a-8788-b8f9875f20c6', NULL, 'Housing', 'expense', '🏠', '#7C3AED', NULL, true, 1, '2025-12-28 16:52:17.237595+00', true, true, NULL),
	('595e0c9d-5743-4fa8-a83f-f9f0bfa3d14b', NULL, 'Other Expense', 'expense', '📦', '#DDD6FE', NULL, true, 99, '2025-12-28 16:52:17.237595+00', true, true, NULL),
	('b17084c4-9890-42c7-b331-a06a51913eb6', NULL, 'Health', 'expense', '💊', '#4C1D95', NULL, true, 7, '2025-12-28 16:52:17.237595+00', true, true, NULL),
	('71c8ee80-a760-436c-ab04-c6007c085cb2', NULL, 'Refunds', 'income', '↩️', '#065F46', NULL, true, 6, '2025-12-28 16:52:17.237595+00', true, true, NULL),
	('1280ab11-93e7-4b6a-bceb-baca7fc50e2e', NULL, 'Utilities', 'expense', '💡', '#C4B5FD', NULL, true, 4, '2025-12-28 16:52:17.237595+00', true, true, NULL),
	('93a5a774-4f4a-446c-891c-489c5f5cb6a4', NULL, 'Technology', 'expense', '🖥️', '#7C3AED', NULL, true, 0, '2026-03-31 21:29:09+00', false, true, NULL);

-- Service templates
INSERT INTO "public"."service_templates" ("id", "name", "icon", "logo_url", "category_id", "service_type", "default_amount", "country_code", "currency", "localized_amount", "billing_cycle", "website_url", "price_selector", "last_price_update", "is_active", "display_order", "created_at", "updated_at") VALUES
	('718afc24-49b7-4071-94b2-7fd6c37d4609', 'Netflix', '📺', NULL, '8079e9fa-5566-4b86-b7dd-dea8076badfd', 'streaming', 6.99, 'PE', 'PEN', 24.90, 'monthly', 'https://www.netflix.com/pe/', NULL, NULL, true, 1, '2025-12-28 16:52:17.237595+00', '2025-12-28 16:52:17.237595+00'),
	('e0f270ad-8d6b-4d23-823d-9fb0a8e0d5b9', 'Disney+', '🏰', NULL, '8079e9fa-5566-4b86-b7dd-dea8076badfd', 'streaming', 7.99, 'PE', 'PEN', 29.90, 'monthly', 'https://www.disneyplus.com/pe/', NULL, NULL, true, 2, '2025-12-28 16:52:17.237595+00', '2025-12-28 16:52:17.237595+00'),
	('0ad7721a-c5e2-4a1d-9acd-6340effb3a08', 'HBO Max', '🎬', NULL, '8079e9fa-5566-4b86-b7dd-dea8076badfd', 'streaming', 9.99, 'PE', 'PEN', 29.90, 'monthly', 'https://www.max.com/', NULL, NULL, true, 3, '2025-12-28 16:52:17.237595+00', '2025-12-28 16:52:17.237595+00'),
	('8a2d7c46-c3e9-4fd4-8eca-7e8897886beb', 'Amazon Prime Video', '📦', NULL, '8079e9fa-5566-4b86-b7dd-dea8076badfd', 'streaming', 8.99, 'PE', 'PEN', 19.90, 'monthly', 'https://www.primevideo.com/', NULL, NULL, true, 4, '2025-12-28 16:52:17.237595+00', '2025-12-28 16:52:17.237595+00'),
	('d244a5cb-17e4-43aa-8408-c9e57b8c5691', 'Star+', '⭐', NULL, '8079e9fa-5566-4b86-b7dd-dea8076badfd', 'streaming', 9.99, 'PE', 'PEN', 37.90, 'monthly', 'https://www.starplus.com/', NULL, NULL, true, 5, '2025-12-28 16:52:17.237595+00', '2025-12-28 16:52:17.237595+00'),
	('f1202985-f9d8-472c-8fdf-0a4ed501f68b', 'Paramount+', '⛰️', NULL, '8079e9fa-5566-4b86-b7dd-dea8076badfd', 'streaming', 5.99, 'PE', 'PEN', 14.90, 'monthly', 'https://www.paramountplus.com/', NULL, NULL, true, 6, '2025-12-28 16:52:17.237595+00', '2025-12-28 16:52:17.237595+00'),
	('e84c1cbb-bd43-4ea9-b3bb-35246830b5ce', 'Crunchyroll', '🍥', NULL, '8079e9fa-5566-4b86-b7dd-dea8076badfd', 'streaming', 7.99, 'PE', 'PEN', 20.90, 'monthly', 'https://www.crunchyroll.com/', NULL, NULL, true, 7, '2025-12-28 16:52:17.237595+00', '2025-12-28 16:52:17.237595+00'),
	('422175af-4019-48c5-bbbd-442a16a85132', 'Apple TV+', '🍎', NULL, '8079e9fa-5566-4b86-b7dd-dea8076badfd', 'streaming', 9.99, 'PE', 'PEN', 24.90, 'monthly', 'https://tv.apple.com/', NULL, NULL, true, 8, '2025-12-28 16:52:17.237595+00', '2025-12-28 16:52:17.237595+00'),
	('e6738f02-baa1-42db-840e-b203dc5baa08', 'Spotify Premium', '🎵', NULL, '8079e9fa-5566-4b86-b7dd-dea8076badfd', 'music', 10.99, 'PE', 'PEN', 18.90, 'monthly', 'https://www.spotify.com/pe/', NULL, NULL, true, 1, '2025-12-28 16:52:17.237595+00', '2025-12-28 16:52:17.237595+00'),
	('c900a0e5-98d7-4205-bda3-b50cd1a44174', 'YouTube Premium', '▶️', NULL, '8079e9fa-5566-4b86-b7dd-dea8076badfd', 'music', 13.99, 'PE', 'PEN', 22.90, 'monthly', 'https://www.youtube.com/premium', NULL, NULL, true, 2, '2025-12-28 16:52:17.237595+00', '2025-12-28 16:52:17.237595+00'),
	('5ced4b88-32dc-4156-80cf-1573a6abfcdc', 'Apple Music', '🍎', NULL, '8079e9fa-5566-4b86-b7dd-dea8076badfd', 'music', 10.99, 'PE', 'PEN', 16.90, 'monthly', 'https://music.apple.com/', NULL, NULL, true, 3, '2025-12-28 16:52:17.237595+00', '2025-12-28 16:52:17.237595+00'),
	('a2e39049-21d0-41a0-8ac4-a6939722189e', 'Amazon Music', '🎧', NULL, '8079e9fa-5566-4b86-b7dd-dea8076badfd', 'music', 9.99, 'PE', 'PEN', 18.90, 'monthly', 'https://music.amazon.com/', NULL, NULL, true, 4, '2025-12-28 16:52:17.237595+00', '2025-12-28 16:52:17.237595+00'),
	('42f4350b-06e5-47f9-ae54-2e41ee937766', 'Deezer', '🎶', NULL, '8079e9fa-5566-4b86-b7dd-dea8076badfd', 'music', 10.99, 'PE', 'PEN', 18.90, 'monthly', 'https://www.deezer.com/', NULL, NULL, true, 5, '2025-12-28 16:52:17.237595+00', '2025-12-28 16:52:17.237595+00'),
	('1f08a310-fdc3-4d6a-ad5c-8d4ac74034df', 'iCloud+ 50GB', '☁️', NULL, 'bafc42c8-cc96-438c-a55c-dfc24680d60c', 'cloud', 0.99, 'PE', 'PEN', 3.50, 'monthly', 'https://www.apple.com/icloud/', NULL, NULL, true, 1, '2025-12-28 16:52:17.237595+00', '2025-12-28 16:52:17.237595+00'),
	('c1f12d5d-5a10-4b55-a7f8-c14e9bc60f59', 'iCloud+ 200GB', '☁️', NULL, 'bafc42c8-cc96-438c-a55c-dfc24680d60c', 'cloud', 2.99, 'PE', 'PEN', 9.50, 'monthly', 'https://www.apple.com/icloud/', NULL, NULL, true, 2, '2025-12-28 16:52:17.237595+00', '2025-12-28 16:52:17.237595+00'),
	('c8c78c6b-e7cf-4cc0-a9c8-68e7ee1aea13', 'iCloud+ 2TB', '☁️', NULL, 'bafc42c8-cc96-438c-a55c-dfc24680d60c', 'cloud', 10.99, 'PE', 'PEN', 34.90, 'monthly', 'https://www.apple.com/icloud/', NULL, NULL, true, 3, '2025-12-28 16:52:17.237595+00', '2025-12-28 16:52:17.237595+00'),
	('040f04d2-c64b-4bc2-812a-04b9b25a433e', 'Google One 100GB', '📁', NULL, 'bafc42c8-cc96-438c-a55c-dfc24680d60c', 'cloud', 1.99, 'PE', 'PEN', 6.50, 'monthly', 'https://one.google.com/', NULL, NULL, true, 4, '2025-12-28 16:52:17.237595+00', '2025-12-28 16:52:17.237595+00'),
	('a7244aff-48e7-4ce5-8d1f-fb0d48e0cb3e', 'Google One 200GB', '📁', NULL, 'bafc42c8-cc96-438c-a55c-dfc24680d60c', 'cloud', 2.99, 'PE', 'PEN', 9.90, 'monthly', 'https://one.google.com/', NULL, NULL, true, 5, '2025-12-28 16:52:17.237595+00', '2025-12-28 16:52:17.237595+00'),
	('93e8800c-e364-42f2-9a56-b47b361aff8b', 'Dropbox Plus', '📦', NULL, 'bafc42c8-cc96-438c-a55c-dfc24680d60c', 'cloud', 11.99, 'PE', 'USD', 11.99, 'monthly', 'https://www.dropbox.com/', NULL, NULL, true, 6, '2025-12-28 16:52:17.237595+00', '2025-12-28 16:52:17.237595+00'),
	('d9a760e7-9eda-4412-8f27-313fce93572d', 'OneDrive 100GB', '📂', NULL, 'bafc42c8-cc96-438c-a55c-dfc24680d60c', 'cloud', 1.99, 'PE', 'USD', 1.99, 'monthly', 'https://onedrive.live.com/', NULL, NULL, true, 7, '2025-12-28 16:52:17.237595+00', '2025-12-28 16:52:17.237595+00'),
	('1879baab-b3b9-462d-b8be-9b7cda326263', 'PlayStation Plus Essential', '🎮', NULL, '8079e9fa-5566-4b86-b7dd-dea8076badfd', 'gaming', 9.99, 'PE', 'PEN', 29.90, 'monthly', 'https://www.playstation.com/ps-plus/', NULL, NULL, true, 1, '2025-12-28 16:52:17.237595+00', '2025-12-28 16:52:17.237595+00'),
	('8866642c-5e55-417b-b5b6-d55d0c9cd4a0', 'PlayStation Plus Extra', '🎮', NULL, '8079e9fa-5566-4b86-b7dd-dea8076badfd', 'gaming', 14.99, 'PE', 'PEN', 52.90, 'monthly', 'https://www.playstation.com/ps-plus/', NULL, NULL, true, 2, '2025-12-28 16:52:17.237595+00', '2025-12-28 16:52:17.237595+00'),
	('5600ff92-ebca-447b-a798-a46e0cba37cf', 'Xbox Game Pass Core', '🎮', NULL, '8079e9fa-5566-4b86-b7dd-dea8076badfd', 'gaming', 9.99, 'PE', 'PEN', 29.90, 'monthly', 'https://www.xbox.com/gamepass', NULL, NULL, true, 3, '2025-12-28 16:52:17.237595+00', '2025-12-28 16:52:17.237595+00'),
	('18719a1c-feaa-4922-a611-84ef57b3c668', 'Xbox Game Pass Ultimate', '🎮', NULL, '8079e9fa-5566-4b86-b7dd-dea8076badfd', 'gaming', 16.99, 'PE', 'PEN', 64.90, 'monthly', 'https://www.xbox.com/gamepass', NULL, NULL, true, 4, '2025-12-28 16:52:17.237595+00', '2025-12-28 16:52:17.237595+00'),
	('c9d14b6e-a706-491d-b67b-ca17ad2af834', 'Nintendo Switch Online', '🕹️', NULL, '8079e9fa-5566-4b86-b7dd-dea8076badfd', 'gaming', 3.99, 'PE', 'USD', 3.99, 'monthly', 'https://www.nintendo.com/switch/online/', NULL, NULL, true, 5, '2025-12-28 16:52:17.237595+00', '2025-12-28 16:52:17.237595+00'),
	('330d6080-f1d1-429f-8986-1220835a98f3', 'EA Play', '⚽', NULL, '8079e9fa-5566-4b86-b7dd-dea8076badfd', 'gaming', 4.99, 'PE', 'USD', 4.99, 'monthly', 'https://www.ea.com/ea-play', NULL, NULL, true, 6, '2025-12-28 16:52:17.237595+00', '2025-12-28 16:52:17.237595+00'),
	('4cc8c5ac-562e-4ae6-96ae-3df80ab87126', 'Microsoft 365 Personal', '📊', NULL, 'bafc42c8-cc96-438c-a55c-dfc24680d60c', 'productivity', 9.99, 'PE', 'PEN', 29.00, 'monthly', 'https://www.microsoft.com/microsoft-365', NULL, NULL, true, 1, '2025-12-28 16:52:17.237595+00', '2025-12-28 16:52:17.237595+00'),
	('8e2ecced-2e06-43fb-be77-06a5e38aea33', 'Microsoft 365 Family', '📊', NULL, 'bafc42c8-cc96-438c-a55c-dfc24680d60c', 'productivity', 12.99, 'PE', 'PEN', 39.00, 'monthly', 'https://www.microsoft.com/microsoft-365', NULL, NULL, true, 2, '2025-12-28 16:52:17.237595+00', '2025-12-28 16:52:17.237595+00'),
	('4bdc293b-4678-4a4e-9c58-489cd036de49', 'ChatGPT Plus', '🤖', NULL, 'bafc42c8-cc96-438c-a55c-dfc24680d60c', 'productivity', 20.00, 'PE', 'USD', 20.00, 'monthly', 'https://chat.openai.com/', NULL, NULL, true, 3, '2025-12-28 16:52:17.237595+00', '2025-12-28 16:52:17.237595+00'),
	('45b7f732-6800-40f9-a63c-bcd2e775269f', 'Claude Pro', '🧠', NULL, 'bafc42c8-cc96-438c-a55c-dfc24680d60c', 'productivity', 20.00, 'PE', 'USD', 20.00, 'monthly', 'https://claude.ai/', NULL, NULL, true, 4, '2025-12-28 16:52:17.237595+00', '2025-12-28 16:52:17.237595+00'),
	('d8a839bd-df43-4e00-a3b2-d57d903c2f83', 'Notion Plus', '📝', NULL, 'bafc42c8-cc96-438c-a55c-dfc24680d60c', 'productivity', 10.00, 'PE', 'USD', 10.00, 'monthly', 'https://www.notion.so/', NULL, NULL, true, 5, '2025-12-28 16:52:17.237595+00', '2025-12-28 16:52:17.237595+00'),
	('2a87a372-3b44-4a83-bc18-adf5f270368c', 'Todoist Pro', '✅', NULL, 'bafc42c8-cc96-438c-a55c-dfc24680d60c', 'productivity', 4.00, 'PE', 'USD', 4.00, 'monthly', 'https://todoist.com/', NULL, NULL, true, 6, '2025-12-28 16:52:17.237595+00', '2025-12-28 16:52:17.237595+00'),
	('42946f41-dcb2-417b-9ff3-6025dcd9ab2c', 'Canva Pro', '🎨', NULL, 'bafc42c8-cc96-438c-a55c-dfc24680d60c', 'productivity', 12.99, 'PE', 'USD', 12.99, 'monthly', 'https://www.canva.com/', NULL, NULL, true, 7, '2025-12-28 16:52:17.237595+00', '2025-12-28 16:52:17.237595+00'),
	('5bc6972c-4561-4c67-93ab-3f18747997cb', 'Figma Professional', '🖼️', NULL, 'bafc42c8-cc96-438c-a55c-dfc24680d60c', 'productivity', 15.00, 'PE', 'USD', 15.00, 'monthly', 'https://www.figma.com/', NULL, NULL, true, 8, '2025-12-28 16:52:17.237595+00', '2025-12-28 16:52:17.237595+00'),
	('3ef8d28f-3873-4430-a92c-c474fd9af85a', 'GitHub Pro', '🐙', NULL, 'bafc42c8-cc96-438c-a55c-dfc24680d60c', 'productivity', 4.00, 'PE', 'USD', 4.00, 'monthly', 'https://github.com/', NULL, NULL, true, 9, '2025-12-28 16:52:17.237595+00', '2025-12-28 16:52:17.237595+00'),
	('fdd40d5f-1896-494e-86f2-a41397915f16', '1Password', '🔐', NULL, 'bafc42c8-cc96-438c-a55c-dfc24680d60c', 'productivity', 2.99, 'PE', 'USD', 2.99, 'monthly', 'https://1password.com/', NULL, NULL, true, 10, '2025-12-28 16:52:17.237595+00', '2025-12-28 16:52:17.237595+00'),
	('da7d6aa9-26bc-4c46-977e-19bb084b3336', 'Movistar Internet', '🌐', NULL, '1280ab11-93e7-4b6a-bceb-baca7fc50e2e', 'utilities', 24.00, 'PE', 'PEN', 89.90, 'monthly', 'https://www.movistar.com.pe/', NULL, NULL, true, 1, '2025-12-28 16:52:17.237595+00', '2025-12-28 16:52:17.237595+00'),
	('18375d19-bbfc-4599-9d33-5aad1f6c7484', 'Claro Internet', '🌐', NULL, '1280ab11-93e7-4b6a-bceb-baca7fc50e2e', 'utilities', 21.00, 'PE', 'PEN', 79.90, 'monthly', 'https://www.claro.com.pe/', NULL, NULL, true, 2, '2025-12-28 16:52:17.237595+00', '2025-12-28 16:52:17.237595+00'),
	('30f4c85f-4c5d-45f7-9889-57733979fa65', 'Entel Móvil', '📱', NULL, '1280ab11-93e7-4b6a-bceb-baca7fc50e2e', 'utilities', 13.00, 'PE', 'PEN', 49.90, 'monthly', 'https://www.entel.pe/', NULL, NULL, true, 3, '2025-12-28 16:52:17.237595+00', '2025-12-28 16:52:17.237595+00'),
	('cccc19ff-97c8-490f-925f-b61c96504465', 'Bitel Móvil', '📱', NULL, '1280ab11-93e7-4b6a-bceb-baca7fc50e2e', 'utilities', 9.00, 'PE', 'PEN', 35.00, 'monthly', 'https://www.bitel.com.pe/', NULL, NULL, true, 4, '2025-12-28 16:52:17.237595+00', '2025-12-28 16:52:17.237595+00');

COMMIT;
