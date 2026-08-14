"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { clearSession, getUser, type User } from "@/lib/api";

export default function Nav() {
  const [user, setUser] = useState<User | null>(null);
  const router = useRouter();
  const pathname = usePathname();

  // Re-read on navigation so the header updates after login/logout.
  useEffect(() => {
    setUser(getUser());
  }, [pathname]);

  return (
    <nav className="nav">
      <Link href="/" className="brand">
        edugen<span>.live</span>
      </Link>
      <div className="nav-links">
        {user ? (
          <>
            <Link href="/dashboard">Subjects</Link>
            <Link href="/proficiency">My proficiency</Link>
            <span className="muted small">{user.email}</span>
            <button
              onClick={() => {
                clearSession();
                setUser(null);
                router.push("/login");
              }}
            >
              Sign out
            </button>
          </>
        ) : (
          <>
            <Link href="/login">Sign in</Link>
            <Link href="/register" className="btn primary">
              Create account
            </Link>
          </>
        )}
      </div>
    </nav>
  );
}
