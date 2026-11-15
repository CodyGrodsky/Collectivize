// Starts the 3D dot logo on the home page (index.html). Until it's ready, or
// if it can't run, the spinning logo image inside the box stays.
import { initLogo3D } from "./logo3d.js";

const box = document.getElementById("logo3d");
initLogo3D({ container: box, logoSrc: "/public/brand/logo.png" })
    .then(() => box.classList.add("is-3d"))
    .catch((error) => console.warn("3D logo unavailable; showing the image.", error));
