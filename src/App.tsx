import { useEffect } from "react";
import {
  BrowserRouter,
  Navigate,
  Outlet,
  Route,
  Routes,
  useLocation,
} from "react-router-dom";
import { Spine, TopBar } from "./components/Shell";
import { Landing } from "./screens/Landing";
import { Paste } from "./screens/Paste";
import { Gallery } from "./screens/Gallery";
import { Rules } from "./screens/Rules";
import { Questions } from "./screens/Questions";
import { Surfaces } from "./screens/Surfaces";
import { ScanConfig } from "./screens/ScanConfig";
import { Scanning } from "./screens/Scanning";
import { Report } from "./screens/Report";
import { BreakDetail } from "./screens/BreakDetail";
import { Gaps } from "./screens/Gaps";
import { Fixes } from "./screens/Fixes";
import { History } from "./screens/History";

function ScrollTop() {
  const { pathname } = useLocation();
  useEffect(() => { window.scrollTo(0, 0); }, [pathname]);
  return null;
}

function AppLayout() {
  return (
    <>
      <TopBar />
      <div className="appgrid">
        <Spine />
        <main className="appmain">
          <Outlet />
        </main>
      </div>
    </>
  );
}

function PlainLayout() {
  return (
    <>
      <TopBar />
      <Outlet />
    </>
  );
}

export function App() {
  return (
    <BrowserRouter>
      <ScrollTop />
      <Routes>
        <Route element={<PlainLayout />}>
          <Route path="/" element={<Landing />} />
          <Route path="/paste" element={<Paste />} />
          <Route path="/examples" element={<Gallery />} />
        </Route>

        <Route path="/e/:slug" element={<AppLayout />}>
          <Route index element={<Navigate to="rules" replace />} />
          <Route path="rules" element={<Rules />} />
          <Route path="questions" element={<Questions />} />
          <Route path="surfaces" element={<Surfaces />} />
          <Route path="config" element={<ScanConfig />} />
          <Route path="scanning" element={<Scanning />} />
          <Route path="report" element={<Report />} />
          <Route path="report/:breakId" element={<BreakDetail />} />
          <Route path="gaps" element={<Gaps />} />
          <Route path="fixes" element={<Fixes />} />
          <Route path="history" element={<History />} />
        </Route>

        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
