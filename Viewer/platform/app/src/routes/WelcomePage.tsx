import React from 'react';
import { Link } from 'react-router-dom';

const EMPTY_GENERATIVE_STUDY_UID =
  '1.2.826.0.1.3680043.8.498.92334923612841918328708913924036869452';
const BACKEND_CANDIDATES = [
  'http://localhost:5000',
  'http://localhost:8000',
  'http://149.165.154.176:5000',
];

export default function WelcomePage() {
  const runtimeConfig = (window as any)?.config;
  const defaultDataSourceName =
    (typeof window !== 'undefined' && runtimeConfig?.defaultDataSourceName) || 'dicomweb';
  const activeDataSource = runtimeConfig?.dataSources?.find(
    ds => ds?.sourceName === defaultDataSourceName
  );
  const activeQidoRoot = activeDataSource?.configuration?.qidoRoot || 'N/A';
  const startUrl = `/studies?datasources=${defaultDataSourceName}`;

  const ensureGenerativePlaceholder = async () => {
    for (const baseUrl of BACKEND_CANDIDATES) {
      try {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 2500);
        const response = await fetch(`${baseUrl}/bootstrap/generative-ai-empty-study`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          signal: controller.signal,
        });
        clearTimeout(timeout);
        if (response.ok) {
          return true;
        }
      } catch (error) {
        // Try the next candidate if this backend is unreachable.
      }
    }
    return false;
  };

  const handleStart = () => {
    ensureGenerativePlaceholder().catch(() => {
      // Continue to study list even if bootstrap fails.
    });
    try {
      localStorage.setItem('generativeAIPlaceholderStudyUID', EMPTY_GENERATIVE_STUDY_UID);
    } catch (error) {
      // Ignore storage errors.
    }
  };

  return (
    <div className="relative min-h-screen bg-[#0f1720] text-white">
      <div className="absolute right-4 top-4 rounded border border-cyan-700 bg-[#06131f] px-3 py-2 text-xs text-cyan-300 md:right-6 md:top-6">
        Active QIDO: {activeQidoRoot}
      </div>
      <div className="mx-auto flex min-h-screen w-full max-w-5xl flex-col items-center justify-center px-6 py-10">
        <h1 className="text-primary-light mb-4 text-center text-5xl font-semibold tracking-tight">
          Text2Med Imaging
        </h1>
        <p className="mb-8 max-w-4xl text-center text-2xl leading-relaxed text-gray-200">
          A research platform integrating generative AI models to transform clinical reports into
          medical images, including CT volumes and X-ray projections, within a DICOM-compatible
          environment.
        </p>

        <div className="flex flex-wrap items-center justify-center gap-4">
          <Link
            to={startUrl}
            onClick={handleStart}
            className="rounded bg-blue-600 px-8 py-4 text-base font-semibold uppercase tracking-wide text-white hover:bg-blue-500"
          >
            Start
          </Link>
        </div>
      </div>

      <img
        src="/assets/welcome/unicampus-logo.png"
        alt="Universita Campus Bio-Medico di Roma"
        className="pointer-events-none absolute bottom-5 left-5 w-24 select-none md:bottom-6 md:left-6 md:w-32"
      />
      <p className="absolute bottom-5 right-4 whitespace-nowrap text-right text-sm font-normal text-gray-300 md:bottom-6 md:right-6">
        This platform integrates components of OHIF Viewer (&copy; OHIF, MIT License). The software has
        been modified and extended for research purposes.
      </p>
    </div>
  );
}

