on run argv
  if (count of argv) is 0 then
    return
  end if
  my forwardURL(item 1 of argv)
end run

on open location this_URL
  my forwardURL(this_URL)
end open location

on forwardURL(this_URL)
  set appPath to POSIX path of (path to me)
  set runnerPath to quoted form of (appPath & "Contents/Resources/run-helper.sh")
  set quotedURL to quoted form of this_URL
  do shell script "/bin/bash " & runnerPath & " " & quotedURL & " >/dev/null 2>&1 &"
end forwardURL
