/**
 * AutoGrade — Firebase Client SDK Configuration
 * 
 * Provides Firebase Authentication and Firestore instance.
 * Credentials loaded from Vite environment variables (VITE_FIREBASE_*).
 */

import { initializeApp, getApps } from "firebase/app";
import { getAuth } from "firebase/auth";
import { getFirestore } from "firebase/firestore";

const firebaseConfig = {
  apiKey: import.meta.env.VITE_FIREBASE_API_KEY || "AIzaSyDummyKeyForDevelopmentMode00",
  authDomain: import.meta.env.VITE_FIREBASE_AUTH_DOMAIN || "autograde-dev.firebaseapp.com",
  projectId: import.meta.env.VITE_FIREBASE_PROJECT_ID || "autograde-dev",
  storageBucket: import.meta.env.VITE_FIREBASE_STORAGE_BUCKET || "autograde-dev.appspot.com",
  messagingSenderId: import.meta.env.VITE_FIREBASE_MESSAGING_SENDER_ID || "1234567890",
  appId: import.meta.env.VITE_FIREBASE_APP_ID || "1:1234567890:web:abcdef123456",
};

// Initialize Firebase once
const app = getApps().length === 0 ? initializeApp(firebaseConfig) : getApps()[0];

export const auth = getAuth(app);
export const db = getFirestore(app);

export default app;
