import type { Role } from "@prisma/client";

// The single source of truth for "what can this role do" — every admin-only
// route imports `can()` from here instead of re-deciding `role === "admin"`
// inline. Two roles today; adding a third (e.g. "moderator") means adding
// one entry here, not hunting through every route file.
export type Permission =
  | "chat.manage_own" // create/rename/delete/edit-message on your own chats
  | "chat.upload_own" // RAG upload + Tarjuman translate on your own chats
  | "admin.view_dashboard" // GET /api/admin/stats — see every user + analytics
  | "admin.toggle_user" // suspend / reactivate any account
  | "admin.create_chat_for_user"; // start a chat owned by someone else

const ROLE_PERMISSIONS: Record<Role, Permission[]> = {
  user: ["chat.manage_own", "chat.upload_own"],
  admin: [
    "chat.manage_own",
    "chat.upload_own",
    "admin.view_dashboard",
    "admin.toggle_user",
    "admin.create_chat_for_user",
  ],
};

export function can(role: Role, permission: Permission): boolean {
  return ROLE_PERMISSIONS[role].includes(permission);
}
