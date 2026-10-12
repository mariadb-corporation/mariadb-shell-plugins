<#--
  Copyright (c) 2026, MariaDB plc.

  This program is free software; you can redistribute it and/or modify
  it under the terms of the GNU General Public License, version 2.0,
  as published by the Free Software Foundation.

  This program is distributed in the hope that it will be useful, but
  WITHOUT ANY WARRANTY; without even the implied warranty of
  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See
  the GNU General Public License, version 2.0, for more details.

  You should have received a copy of the GNU General Public License
  along with this program; if not, write to the Free Software Foundation, Inc.,
  51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA
-->
<#-- The page frame of every page of this theme: the base theme's template with
     its scripts and hooks kept as they are, laid out like the MCP server's own
     sign-in page (logo, welcome header, card, footer). -->
<#import "theme-resources.ftl" as themeResourceTags>
<#macro registrationLayout bodyClass="" displayInfo=false displayMessage=true displayRequiredFields=false>
<!DOCTYPE html>
<html lang="${lang}"<#if realm.internationalizationEnabled> dir="${(locale.rtl)?then('rtl','ltr')}"</#if>>

<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <meta name="robots" content="noindex, nofollow">
    <title>${msg("mcpWelcomeTitle")}</title>
    <#if properties.styles?has_content>
        <#list properties.styles?split(' ') as style>
            <link href="${url.resourcesPath}/${style}" rel="stylesheet" />
        </#list>
    </#if>
    <script type="importmap">
        {
            "imports": {
                "rfc4648": "${url.resourcesCommonPath}/vendor/rfc4648/rfc4648.js"
            }
        }
    </script>
    <script src="${url.resourcesPath}/js/menu-button-links.js" type="module"></script>
    <#if scripts??>
        <#list scripts as script>
            <script src="${script}" type="text/javascript"></script>
        </#list>
    </#if>
    <script type="module">
        <#outputformat "JavaScript">
        import { startSessionPolling } from ${(url.resourcesPath + "/js/authChecker.js")?c};

        startSessionPolling(
            ${url.ssoLoginInOtherTabsUrl?c}
        );
        </#outputformat>
    </script>
    <script type="module">
        document.addEventListener("click", (event) => {
            const link = event.target.closest("a[data-once-link]");

            if (!link) {
                return;
            }

            if (link.getAttribute("aria-disabled") === "true") {
                event.preventDefault();
                return;
            }

            link.setAttribute("role", "link");
            link.setAttribute("aria-disabled", "true");
        });
    </script>
    <#if authenticationSession??>
        <script type="module">
            <#outputformat "JavaScript">
            import { checkAuthSession } from ${(url.resourcesPath + "/js/authChecker.js")?c};

            checkAuthSession(
                ${authenticationSession.authSessionIdHash?c}
            );
            </#outputformat>
        </script>
    </#if>
</head>

<body data-page-id="login-${pageId}">
<div id="root">
    <div class="welcomeLogo"><#include "logo.ftl"></div>
    <div class="welcomeText">
        <h2>${msg("mcpWelcomeHeading")}</h2>
        <p>${msg("mcpWelcomeText")}</p>
    </div>

    <div class="mcpCard">
        <#if realm.internationalizationEnabled && locale.supported?size gt 1>
            <div id="kc-locale" class="menu-button-links">
                <button id="kc-current-locale-link" class="mcpButton" aria-label="${msg("languages")}" aria-haspopup="true" aria-expanded="false" aria-controls="language-switch1">${locale.current}</button>
                <ul role="menu" tabindex="-1" aria-labelledby="kc-current-locale-link" id="language-switch1" class="mcpHidden">
                    <#list locale.supported as l>
                        <li role="none"><a role="menuitem" href="${l.url}">${l.label}</a></li>
                    </#list>
                </ul>
            </div>
        </#if>

        <#if !(auth?has_content && auth.showUsername() && !auth.showResetCredentials())>
            <p id="kc-page-title"><#nested "header"></p>
        <#else>
            <#nested "show-username">
            <div id="kc-username">
                <span id="kc-attempted-username">${auth.attemptedUsername}</span>
                <a id="reset-login" href="${url.loginRestartFlowUrl}" aria-label="${msg("restartLoginTooltip")}" title="${msg("restartLoginTooltip")}">
                    <span class="kc-tooltip-text">${msg("restartLoginTooltip")}</span>
                </a>
            </div>
        </#if>

        <#if client?? && client.clientId?? && !(client.clientId?starts_with("account") || client.clientId?starts_with("security-admin"))>
            <div class="mcpIntro">${msg("mcpContinueTo")} <strong>${(client.name?has_content)?then(advancedMsg(client.name), client.clientId)}</strong></div>
        </#if>

        <div id="kc-content">
            <div id="kc-content-wrapper">
                <#-- App-initiated actions should not see warning messages about the need to complete the action during login. -->
                <#if displayMessage && message?has_content && (message.type != 'warning' || !isAppInitiatedAction??)>
                    <#if message.type = 'error'>
                        <div class="mrsLoginError" role="alert"><p>${kcSanitize(message.summary)?no_esc}</p></div>
                    <#else>
                        <div class="mcpAlert alert-${message.type}" role="status">${kcSanitize(message.summary)?no_esc}</div>
                    </#if>
                </#if>

                <#nested "form">

                <#if auth?has_content && auth.showTryAnotherWayLink()>
                    <form id="kc-select-try-another-way-form" action="${url.loginAction}" method="post">
                        <input type="hidden" name="tryAnotherWay" value="on"/>
                        <a href="#" id="try-another-way" class="mcpLinks"
                           onclick="document.forms['kc-select-try-another-way-form'].requestSubmit();return false;">${msg("doTryAnotherWay")}</a>
                    </form>
                </#if>

                <#if switchOrganizationEnabled?? && switchOrganizationEnabled>
                    <form id="kc-switch-organization-form" action="${url.loginAction}" method="post">
                        <input type="hidden" name="switchOrganization" value="true"/>
                        <a href="#" id="switch-organization" class="mcpLinks"
                           onclick="document.forms['kc-switch-organization-form'].requestSubmit();return false;">${msg("doSwitchOrganization")}</a>
                    </form>
                </#if>

                <#nested "socialProviders">

                <#if displayInfo>
                    <div id="kc-info" class="mcpLinks"><#nested "info"></div>
                </#if>

                <div class="mrsLoginSeparator"></div>
            </div>
        </div>
    </div>

    <div class="welcomeSpacer"></div>
    <div class="footer">${msg("mcpFooter")}</div>
</div>
</body>
</html>
</#macro>
