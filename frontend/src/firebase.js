/**
 * AutoGrade — Firebase Client SDK Configuration
 * 
 * Provides Firebase Authentication and Firestore instance.
 * Credentials loaded from Vite environment variables (VITE_FIREBASE_*).
 */

import { initializeApp, getApps } from "firebase/app";
import { connectAuthEmulator, getAuth } from "firebase/auth";
import { connectFirestoreEmulator, getFirestore } from "firebase/firestore";

const firebaseConfig = {
  apiKey: import.meta.env.VITE_FIREBASE_API_KEY || "AIzaSyDummyKeyForDevelopmentMode00",
  authDomain: import.meta.env.VITE_FIREBASE_AUTH_DOMAIN || "autograde-dev.firebaseapp.com",
  projectId: import.meta.env.VITE_FIREBASE_PROJECT_ID || "autograde-dev",
  storageBucket: import.meta.env.VITE_FIREBASE_STORAGE_BUCKET || "autograde-dev.appspot.com",
  messagingSenderId: import.meta.env.VITE_FIREBASE_MESSAGING_SENDER_ID || "1234567890",
  appId: import.meta.env.VITE_FIREBASE_APP_ID || "1:1234567890:web:abcdef123456",
};

// Emulator mode (npm run dev:emulator): a "demo-" project cannot reach real Firebase,
// so no real project settings are used at all.
const firestoreEmulatorHost = import.meta.env.VITE_FIRESTORE_EMULATOR_HOST;
const authEmulatorUrl = import.meta.env.VITE_AUTH_EMULATOR_URL;
const useEmulators = Boolean(firestoreEmulatorHost && authEmulatorUrl);
const emulatorConfig = {
  apiKey: "demo-key",
  authDomain: "demo-autograd.firebaseapp.com",
  projectId: "demo-autograd",
  appId: "demo-app",
};

// Initialize Firebase once
const isFirstInit = getApps().length === 0;
const app = isFirstInit
  ? initializeApp(useEmulators ? emulatorConfig : firebaseConfig)
  : getApps()[0];

export const auth = getAuth(app);
export const db = getFirestore(app);

if (useEmulators && isFirstInit) {
  const [host, port] = firestoreEmulatorHost.split(":");
  connectFirestoreEmulator(db, host, Number(port));
  connectAuthEmulator(auth, authEmulatorUrl, { disableWarnings: true });
}

export default app;
